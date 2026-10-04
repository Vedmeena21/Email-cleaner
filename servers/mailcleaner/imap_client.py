"""imaplib wrapper. Everything works in UIDs rather than sequence numbers,
so the mailbox shifting under us can't make us hit the wrong message.

Vendored from darcodev/email-cleaner @ 542e515 (MIT, see LICENSE-email-cleaner
and THIRD_PARTY.md). Changed for mail-cleaner: every delete path is gone
(move_to_trash, delete_permanently, empty_trash, _expunge, the COPY+\\Deleted
fallback, Trash discovery) along with the body-snippet fetch. Added: folder
listing/creation, Message-ID fetch and a MOVE-only move() that records COPYUID.
This module must never flag, expunge or delete mail; tests/test_no_delete.py
checks that.
"""

from __future__ import annotations

import base64
import contextlib
import email
import email.header
import email.utils
import imaplib
import re
import ssl
import socket
from dataclasses import dataclass, field
from typing import Callable, Iterable

from errors import CleanerError, ConnectionLost, SearchUnsupported

# how many UIDs we put on a single IMAP command line. Bigger batches mean
# fewer round trips (faster scans/moves), kept comfortably under the command
# line length most servers accept.
FETCH_BATCH = 1000
STORE_BATCH = 500
# A full header block is an order of magnitude more data per message than the
# four fields we normally ask for, so those go in much smaller batches. Progress
# is reported per batch, and one batch of 1000 full headers is over half a
# minute of an entirely silent terminal - long enough to look hung and be killed.
FULL_FETCH_BATCH = 100

_HEADER_FIELDS = "(FROM SUBJECT DATE LIST-UNSUBSCRIBE)"
_FETCH_PARTS = f"(UID RFC822.SIZE FLAGS BODY.PEEK[HEADER.FIELDS {_HEADER_FIELDS}])"
# The whole header block, for servers that do not honour the field list above.
# Yahoo returns FROM/SUBJECT/DATE and silently drops LIST-UNSUBSCRIBE from it,
# which reads as "no marketing mail anywhere in this mailbox" - the one header
# the promotional filter runs on. Costs more bytes, so it is not the default.
_FETCH_PARTS_FULL = "(UID RFC822.SIZE FLAGS BODY.PEEK[HEADER])"

_MESSAGES_RE = re.compile(r"MESSAGES (\d+)")
_UID_RE = re.compile(rb"UID (\d+)")
_SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")
_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
_UNSUB_URL_RE = re.compile(r"<([^>]+)>")


@dataclass
class EmailSummary:
    uid: str
    sender_name: str
    sender_email: str
    subject: str
    date: str
    size: int
    flagged: bool
    unsubscribe: list[str] = field(default_factory=list)

    @property
    def sender_display(self) -> str:
        if self.sender_name and self.sender_email:
            return f"{self.sender_name} <{self.sender_email}>"
        return self.sender_email or self.sender_name or "(unknown sender)"


def decode_mime_header(raw: str) -> str:
    """Decode RFC 2047 encoded-words ('=?UTF-8?B?...?=') into readable text."""
    if not raw:
        return ""
    parts = []
    try:
        for chunk, charset in email.header.decode_header(raw):
            if isinstance(chunk, bytes):
                parts.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(chunk)
    except Exception:
        return raw
    return "".join(parts).strip()


def extract_unsubscribe_urls(header_value: str) -> list[str]:
    """Pull URLs out of a List-Unsubscribe header.

    The header looks like: <https://ex.com/unsub?x=1>, <mailto:unsub@ex.com>
    """
    if not header_value:
        return []
    urls = [u.strip() for u in _UNSUB_URL_RE.findall(header_value)]
    # https links first, mailto as a fallback
    urls.sort(key=lambda u: (not u.startswith("http"), u))
    return [u for u in urls if u]


def quote_imap_string(value: str) -> str:
    r"""Quote a string for use in an IMAP command ('a"b' -> '"a\"b"')."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def encode_mailbox_name(name: str) -> str:
    """Encode a folder name as modified UTF-7 (RFC 3501 section 5.1.3).

    IMAP folder names travel as ASCII, so 'Gelöschte' has to go over the wire
    as 'Gel&APY-schte'. An all-ASCII name is returned untouched on purpose:
    names we got back from LIST are already in this encoding, and re-encoding
    would turn their '&' into '&-' and point us at a folder that doesn't exist.
    """
    if name.isascii():
        return name
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        raw = "".join(pending).encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").rstrip("=")
        out.append("&" + b64.replace("/", ",") + "-")
        pending.clear()

    for ch in name:
        if " " <= ch <= "~":  # printable ascii represents itself
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            pending.append(ch)
    flush()
    return "".join(out)


def quoted_mailbox(name: str) -> str:
    """A folder name ready to hand to imaplib: encoded, then quoted."""
    return quote_imap_string(encode_mailbox_name(name))


def search_args(criteria: list[str]) -> list:
    """Prepare SEARCH arguments so non-ASCII terms survive the trip.

    imaplib encodes str arguments as ASCII, so a keyword, sender or Gmail query
    containing an accent raises UnicodeEncodeError before the command is even
    sent. When anything needs more than ASCII we declare CHARSET UTF-8 and hand
    imaplib pre-encoded bytes, which it appends to the command line verbatim.
    Pure ASCII criteria are passed through unchanged so we keep talking to old
    servers exactly as before.
    """
    if all(c.isascii() for c in criteria):
        return list(criteria)
    return ["CHARSET", "UTF-8", *(c.encode("utf-8") for c in criteria)]


def _parse_fetch_response(data: list) -> list[EmailSummary]:
    """Turn imaplib's FETCH response into EmailSummary objects.

    imaplib hands back a cursed mix of tuples (metadata, literal-bytes) and
    stray b')' seperators, we only care about the tuples.
    """
    summaries = []
    # print(data)  # left this in while i was figuring out the response shape
    for item in data:
        if not (isinstance(item, tuple) and len(item) >= 2):
            continue
        meta, header_bytes = item[0], item[1]
        if not isinstance(meta, bytes):
            continue

        uid_m = _UID_RE.search(meta)
        if not uid_m:
            continue
        size_m = _SIZE_RE.search(meta)
        flags_m = _FLAGS_RE.search(meta)
        flags = flags_m.group(1) if flags_m else b""

        msg = email.message_from_bytes(header_bytes or b"")
        sender_name, sender_email = email.utils.parseaddr(msg.get("From", ""))
        date_str = ""
        try:
            parsed = email.utils.parsedate_to_datetime(msg.get("Date", ""))
            if parsed:
                date_str = parsed.strftime("%Y-%m-%d")
        except Exception:
            date_str = (msg.get("Date") or "")[:10]

        summaries.append(
            EmailSummary(
                uid=uid_m.group(1).decode(),
                sender_name=decode_mime_header(sender_name),
                sender_email=sender_email.lower(),
                subject=decode_mime_header(msg.get("Subject", "")) or "(no subject)",
                date=date_str,
                size=int(size_m.group(1)) if size_m else 0,
                flagged=b"\\Flagged" in flags,
                # decoded like the other headers: some senders RFC 2047-encode
                # this one, which hides the angle brackets the URLs live in
                # ('=3Chttps...=3E') and made the whole message read as not
                # promotional - the one header the off-Gmail filter runs on
                unsubscribe=extract_unsubscribe_urls(
                    decode_mime_header(msg.get("List-Unsubscribe", ""))
                ),
            )
        )
    return summaries


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --- added for mail-cleaner -------------------------------------------------

_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\) (?P<delim>"[^"]*"|NIL) (?P<name>.+)$')
_MESSAGE_ID_RE = re.compile(rb"^Message-ID:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _first_int(values) -> int | None:
    for v in values or []:
        if v is None:
            continue
        try:
            return int(v.decode() if isinstance(v, bytes) else v)
        except ValueError:
            continue
    return None


def expand_uid_set(spec: str) -> list[str]:
    """'1:3,7' -> ['1', '2', '3', '7'] (the form COPYUID reports UIDs in)."""
    out: list[str] = []
    for part in spec.split(","):
        if ":" in part:
            a, b = (int(x) for x in part.split(":"))
            lo, hi = min(a, b), max(a, b)
            out.extend(str(n) for n in range(lo, hi + 1))
        elif part:
            out.append(part)
    return out


def _unquote_mailbox(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


class ImapSession:
    """A logged-in IMAP connection plus the handful of ops we need."""

    def __init__(self, host: str, port: int, address: str, password: str):
        self.host = host
        self.port = port
        self.address = address
        self._password = password
        self._imap: imaplib.IMAP4_SSL | None = None
        self._capabilities: set[str] = set()
        self.selected_folder: str | None = None
        self.uidvalidity: int | None = None
        # what STATUS said a folder held just before we opened it. Yahoo gives
        # the real total until the mailbox is selected and the size of its own
        # windowed view afterwards, so open time is the only honest moment.
        self._folder_totals: dict[str, int] = {}
        # set when the server hung up; reconnect() clears it
        self._lost = False

    def connect(self) -> None:
        try:
            self._imap = imaplib.IMAP4_SSL(
                self.host, self.port, ssl_context=ssl.create_default_context(), timeout=30
            )
        # ssl.SSLError is a subclass of OSError, so it has to be caught first.
        # Behind the reachability handler it was dead code, and every TLS
        # failure - a plaintext port like 143 answering the handshake, an
        # expired or untrusted certificate - came out as "could not reach
        # host", telling the user to check an internet connection that was
        # working fine and never mentioning the port they had actually mistyped.
        except ssl.SSLError as exc:
            raise CleanerError(
                f"TLS handshake with {self.host} failed ({exc}).",
                hint=(
                    "The server may not support implicit TLS on this port. "
                    "Most IMAP servers use 993; try --port 993."
                ),
            ) from exc
        except (socket.gaierror, TimeoutError, OSError) as exc:
            raise CleanerError(
                f"Could not reach {self.host}:{self.port} ({exc}).",
                hint="Check your internet connection and the IMAP host name.",
            ) from exc

        try:
            self._imap.login(self.address, self._password)
        except imaplib.IMAP4.error as exc:
            raise CleanerError(
                f"Login failed for {self.address}.",
                hint=(
                    "Regular account passwords usually don't work over IMAP. "
                    "Make sure the MC_<ACCOUNT>_APP_PASSWORD value in .env is an "
                    "app password (see the README for where to create one)."
                ),
            ) from exc

        self._load_capabilities()

    def _load_capabilities(self) -> None:
        """Work out what this server can do, erring towards what it told us.

        Some servers (Gmail included) only advertise their full list after
        login, so the post-login refresh is worth asking for - but it is only
        ever added to what imaplib already parsed from the greeting, never
        swapped in for it. A refresh that comes back NO, or OK with an empty
        payload, used to leave us believing the server supports nothing at all:
        Gmail search off (so a different set of mail matches), MOVE off, and
        worst of all UID EXPUNGE off, which drops the purge to the unscoped
        bare EXPUNGE that takes other clients' pending deletions with it.
        """
        conn = self._conn()
        self._capabilities = {c.upper() for c in conn.capabilities}
        try:
            typ, data = conn.capability()
            if typ == "OK" and data and data[0]:
                self._capabilities |= {c.upper() for c in data[0].decode().split()}
        except imaplib.IMAP4.error:
            pass

    @property
    def supports_gmail_search(self) -> bool:
        return "X-GM-EXT-1" in self._capabilities

    @property
    def supports_move(self) -> bool:
        # RFC 6851. Gmail, Yahoo and most modern servers advertise it. We
        # require it: the alternative (copy + mark deleted + expunge) is a
        # delete, and this module doesn't delete.
        return "MOVE" in self._capabilities

    @property
    def supports_unselect(self) -> bool:
        # RFC 3691. Leaves the mailbox without the implicit purge CLOSE does.
        return "UNSELECT" in self._capabilities

    def close(self) -> None:
        """Hang up. Deliberately never sends IMAP CLOSE.

        CLOSE looks like the polite way out, but on a writable mailbox it
        permanently removes every \\Deleted message in it first. Moves open
        the folder read-write, so ending that way would quietly destroy mail
        another client had flagged and not compacted. UNSELECT (RFC 3691) is
        CLOSE without the purge; where it isn't offered, LOGOUT from the
        selected state ends the session just as cleanly and removes nothing.
        """
        if self._imap is None:
            return
        try:
            if self._imap.state == "SELECTED" and self.supports_unselect:
                self._imap.unselect()
            self._imap.logout()
        except Exception:
            pass
        self._imap = None

    @contextlib.contextmanager
    def _alive(self, doing: str):
        """Turn "the server hung up" into something callers can act on.

        imaplib raises abort from deep inside _command_complete when it reads a
        BYE, and it escaped as a traceback from every fetch and move we make.
        """
        try:
            yield
        except imaplib.IMAP4.abort as exc:
            self._lost = True
            raise ConnectionLost(
                f"The server closed the connection while {doing} ({exc}).",
                hint="Servers cut long sessions off; running it again resumes.",
            ) from exc

    def reconnect(self) -> None:
        """Start a fresh session after the server hung up on us."""
        try:
            if self._imap is not None:
                self._imap.logout()
        except Exception:
            pass
        self._imap = None
        self._lost = False
        self.selected_folder = None
        self._folder_totals.clear()
        self.connect()

    def _conn(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            raise CleanerError("Not connected. This is a bug, please report it.")
        return self._imap

    def select(self, folder: str = "INBOX", readonly: bool = True) -> int:
        """Open a folder, return how many messages the selected view exposes.

        That is not always the same as how many the folder holds - see
        folder_message_count, which is why the count is taken first.
        """
        held = self._status_message_count(folder)
        typ, data = self._conn().select(quoted_mailbox(folder), readonly=readonly)
        if typ != "OK":
            detail = (data[0] or b"").decode(errors="replace") if data else ""
            raise CleanerError(
                f"Could not open folder '{folder}' ({detail}).",
                hint="Check the folder name with imap_list_folders.",
            )
        self.selected_folder = folder
        self.uidvalidity = _first_int(self._conn().response("UIDVALIDITY")[1])
        # first reading wins. Yahoo answers STATUS with the real folder size
        # only until the mailbox has been opened in this session; every later
        # select gets the size of the window back instead, and overwriting with
        # that made the two numbers agree and silently retired the warning that
        # most of the folder is out of reach.
        if held is not None and folder not in self._folder_totals:
            self._folder_totals[folder] = held
        try:
            return int(data[0])
        except (TypeError, ValueError):
            return 0

    def search_gmail_raw(self, query: str) -> list[str]:
        # X-GM-RAW lets us hand Gmail its own search-box syntax
        args = search_args(["X-GM-RAW", quote_imap_string(query)])
        typ, data = self._uid_search(args)
        return self._search_result(typ, data)

    def search_standard(self, criteria: list[str]) -> list[str]:
        typ, data = self._uid_search(search_args(criteria))
        return self._search_result(typ, data)

    def _uid_search(self, args: list) -> tuple:
        """Run UID SEARCH, turning imaplib's exceptions into our own.

        imaplib raises on a BAD reply from inside _command_complete, so the
        status never reaches _search_result and its friendly message. A server
        refusing a search term it does not implement answers exactly that way -
        Yahoo returns "[CANNOT] ... not supported" for any HEADER search - and
        the user got a raw traceback for something the tool can work around.
        """
        try:
            return self._conn().uid("SEARCH", *args)
        except imaplib.IMAP4.abort as exc:
            # the connection itself is gone; retrying on it would only fail again
            self._lost = True
            raise ConnectionLost(
                f"The connection to {self.host} dropped mid-search ({exc}).",
                hint="Servers cut long sessions off; running it again resumes.",
            ) from exc
        except imaplib.IMAP4.error as exc:
            raise SearchUnsupported(
                f"The server rejected this search ({exc}).",
                hint="This server does not implement every IMAP search term.",
            ) from exc

    @staticmethod
    def _search_result(typ: str, data: list) -> list[str]:
        if typ != "OK":
            detail = (data[0] or b"").decode(errors="replace") if data else ""
            hint = None
            if "BADCHARSET" in detail.upper():
                # we only ask for UTF-8 when a term needs it, so this is a
                # server that cannot search outside ascii at all
                hint = "This server can't search non-ASCII text; try an ASCII keyword."
            raise CleanerError(f"Search failed ({detail}).", hint=hint)
        if not data or not data[0]:
            return []
        # UIDs come back oldest first; keep that order so --limit trims
        # the newest messages, not the oldest
        return data[0].decode().split()

    def fetch_summaries(
        self,
        uids: list[str],
        on_progress: Callable[[int, int], None] | None = None,
        full_headers: bool = False,
    ) -> list[EmailSummary]:
        """Header summaries for `uids`.

        full_headers asks for the entire header block instead of the four
        fields we actually read. Only worth it when the caller depends on
        List-Unsubscribe and the server cannot be trusted to include it in a
        HEADER.FIELDS list - see _FETCH_PARTS_FULL.
        """
        summaries: list[EmailSummary] = []
        done = 0
        parts = _FETCH_PARTS_FULL if full_headers else _FETCH_PARTS
        size = FULL_FETCH_BATCH if full_headers else FETCH_BATCH
        # say we have started before the first batch, not after it: on a slow
        # server the first chunk is seconds of otherwise blank terminal, which
        # is exactly long enough for someone to conclude it has hung
        if on_progress and uids:
            on_progress(0, len(uids))
        for batch in _chunks(uids, size):
            with self._alive("reading message headers"):
                typ, data = self._conn().uid("FETCH", ",".join(batch), parts)
            if typ != "OK":
                raise CleanerError("Fetching message headers failed.")
            summaries.extend(_parse_fetch_response(data))
            done += len(batch)
            if on_progress:
                on_progress(min(done, len(uids)), len(uids))
        return summaries

    def _status_message_count(self, folder: str) -> int | None:
        """Ask STATUS how many messages a folder holds. None if it will not say."""
        try:
            typ, data = self._conn().status(quoted_mailbox(folder), "(MESSAGES)")
        except imaplib.IMAP4.error:
            return None
        if typ != "OK" or not data or not data[0]:
            return None
        found = _MESSAGES_RE.search(data[0].decode(errors="replace"))
        return int(found.group(1)) if found else None

    def folder_message_count(self, folder: str) -> int | None:
        """How many messages the folder held when we opened it.

        Worth knowing separately from what a search returns: the two disagree
        on Yahoo, which reports a 235k-message Inbox and then exposes only the
        newest 10000 of it to the selected view. Every search runs against the
        smaller number, so a run that finds nothing is not evidence the mail is
        not there. Asked before SELECT and remembered, because afterwards the
        server answers with the size of the window instead of the folder.
        """
        if folder in self._folder_totals:
            return self._folder_totals[folder]
        return self._status_message_count(folder)

    # --- added for mail-cleaner ---------------------------------------------

    def list_folders(self) -> list[dict]:
        """Every folder as {'name': ..., 'flags': [...]}. Names stay in the
        server's modified UTF-7, which is what select/move expect back."""
        typ, listing = self._conn().list()
        if typ != "OK":
            raise CleanerError("Listing folders failed.")
        folders = []
        for line in listing or []:
            if not isinstance(line, bytes):
                continue
            m = _LIST_RE.match(line.decode(errors="replace"))
            if m:
                folders.append({
                    "name": _unquote_mailbox(m.group("name")),
                    "flags": m.group("flags").split(),
                })
        return folders

    def create_folder(self, name: str) -> bool:
        """Create a top-level folder if it isn't there. Returns True if created."""
        existing = {f["name"].lower() for f in self.list_folders()}
        if encode_mailbox_name(name).lower() in existing:
            return False
        typ, data = self._conn().create(quoted_mailbox(name))
        if typ != "OK":
            detail = (data[0] or b"").decode(errors="replace") if data else ""
            raise CleanerError(f"Creating folder '{name}' failed ({detail}).")
        try:  # webmail UIs often hide unsubscribed folders
            self._conn().subscribe(quoted_mailbox(name))
        except imaplib.IMAP4.error:
            pass
        return True

    def fetch_message_ids(self, uids: list[str]) -> dict[str, str]:
        """uid -> RFC 822 Message-ID, for the action log. Missing ids are left out."""
        out: dict[str, str] = {}
        for batch in _chunks(uids, FETCH_BATCH):
            with self._alive("reading Message-IDs"):
                typ, data = self._conn().uid(
                    "FETCH", ",".join(batch), "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
                )
            if typ != "OK":
                raise CleanerError("Fetching Message-IDs failed.")
            for item in data:
                if not (isinstance(item, tuple) and len(item) >= 2 and isinstance(item[0], bytes)):
                    continue
                uid_m = _UID_RE.search(item[0])
                mid_m = _MESSAGE_ID_RE.search(item[1] or b"")
                if uid_m and mid_m:
                    out[uid_m.group(1).decode()] = mid_m.group(1).decode(errors="replace")
        return out

    def move(self, uids: list[str], target: str) -> dict:
        """UID MOVE `uids` from the selected folder into `target`.

        MOVE only: without the extension we stop rather than fall back to
        copy + delete. Returns {'moved': n, 'target_uidvalidity': int|None,
        'new_uids': {old_uid: new_uid}} (new_uids needs UIDPLUS/COPYUID).
        """
        if not self.supports_move:
            raise CleanerError(
                f"{self.host} doesn't support IMAP MOVE, and mail-cleaner won't "
                "fall back to copy + delete."
            )
        conn = self._conn()
        moved, new_uids, target_validity = 0, {}, None
        for batch in _chunks(uids, STORE_BATCH):
            with self._alive(f"moving messages to '{target}'"):
                typ, data = conn.uid("MOVE", ",".join(batch), quoted_mailbox(target))
            if typ != "OK":
                detail = (data[0] or b"").decode(errors="replace") if data else ""
                raise CleanerError(
                    f"Moving messages to '{target}' failed ({detail}). "
                    f"{moved} message(s) were moved before the error."
                )
            # RFC 6851 sends COPYUID in an untagged OK; some servers put it in
            # the tagged reply instead ('[COPYUID v src dst] Done'). Read both.
            reports = [r for r in conn.response("COPYUID")[1] or [] if r]
            for line in data or []:
                text = line.decode(errors="replace") if isinstance(line, bytes) else str(line or "")
                m = re.search(r"\[COPYUID ([^\]]+)\]", text)
                if m:
                    reports.append(m.group(1))
            for resp in reports:
                parts = (resp.decode() if isinstance(resp, bytes) else str(resp)).split()
                if len(parts) == 3:
                    target_validity = int(parts[0])
                    new_uids.update(zip(expand_uid_set(parts[1]), expand_uid_set(parts[2])))
            moved += len(batch)
        return {"moved": moved, "target_uidvalidity": target_validity, "new_uids": new_uids}

    def __enter__(self) -> "ImapSession":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
