"""Turn a rule into a provider search, and check candidates against it.

Pure functions, no network, so every translation is unit-tested
(tests/test_query.py). The providers/*.md files describe the same mapping for
humans; keep them in step with this file.

    build_plan(rule, family, keep_senders, defaults, today) -> dict
    check_candidate(candidate, post_checks) -> (keep, reason)

`family` is how a provider is searched: "gmail" (Gmail search syntax),
"graph" (Microsoft Graph $filter / KQL $search) or "imap" (IMAP SEARCH).
"""

from __future__ import annotations

import email.utils
import re
from datetime import date, timedelta

from mcconfig import sender_matches

UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}
SIZE_UNITS = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
UNSUPPORTED = {"gmail": set(), "graph": {"category"}, "imap": {"category"}}
LIST_KEYS = ("from", "subject", "contains")


# --- small helpers ------------------------------------------------------------

def as_list(value) -> list:
    return list(value) if isinstance(value, list) else [value]


def duration_days(value: str) -> int:
    return int(value[:-1]) * UNIT_DAYS[value[-1]]


def cutoff(value: str, today: date) -> date:
    return today - timedelta(days=duration_days(value))


def size_bytes(value: str) -> int:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(KB|MB|GB)", value.replace(" ", "").upper())
    if not m:
        raise ValueError(f"bad size {value!r}")
    return int(float(m.group(1)) * SIZE_UNITS[m.group(2)])


def imap_date(d: date) -> str:
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def from_needle(pattern: str) -> str:
    """The searchable part of a sender pattern: 'noreply@*' -> 'noreply@',
    '*@shop.example' / '@shop.example' -> 'shop.example'. Servers match it
    loosely; check_candidate() then applies the exact pattern."""
    p = pattern.strip().lower()
    if p.startswith("*@"):
        p = p[1:]
    if p.startswith("@") or "@" not in p:
        return p.lstrip("@")
    local, _, domain = p.partition("@")
    domain = domain.strip("*.")
    if "*" in local:
        return domain or local.replace("*", "")
    return f"{local}@{domain}" if domain else f"{local}@"


def phrase_in(text: str, phrase: str) -> bool:
    """Whole-word, case-insensitive: 'OTP' matches 'Your OTP' but not 'hotpot'."""
    return re.search(r"(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)", text or "", re.IGNORECASE) is not None


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- unsupported conditions ---------------------------------------------------

def _supported(conds: dict, family: str, notes: list[str]) -> dict | None:
    """Drop what this provider can't search. None means the rule can't run here."""
    missing = UNSUPPORTED[family] & set(conds)
    if missing:
        notes.append(f"skipped: needs {', '.join(sorted(missing))}, which this provider doesn't have")
        return None
    branches = conds.get("any_of")
    if not branches:
        return dict(conds)
    kept = []
    for b in branches:
        gone = UNSUPPORTED[family] & set(b)
        if gone:
            notes.append(f"any_of branch {b} dropped: this provider has no {', '.join(sorted(gone))}")
        else:
            kept.append(b)
    out = {k: v for k, v in conds.items() if k != "any_of"}
    if not kept:
        notes.append("skipped: no any_of branch can run on this provider")
        return None
    if len(kept) == 1:
        out.update(kept[0])
    else:
        out["any_of"] = kept
    return out


# --- Gmail ------------------------------------------------------------------

def _gmail_word(p: str, always_quote: bool = False) -> str:
    return _quote(p) if always_quote or re.search(r"[\s\-]", p) else p


def _gmail_or(terms: list[str]) -> str:
    return terms[0] if len(terms) == 1 else "{" + " ".join(terms) + "}"


def _gmail_items(key: str, value) -> list[str]:
    """One Gmail term per list item (for from/subject/contains)."""
    if key == "from":
        return [f"from:{from_needle(p)}" for p in as_list(value)]
    if key == "subject":
        return [f"subject:{_gmail_word(p)}" for p in as_list(value)]
    return [_gmail_word(p, always_quote=True) for p in as_list(value)]


def _gmail_term(key: str, value) -> str | None:
    if key == "older_than":
        n, unit = int(value[:-1]), value[-1]
        return f"older_than:{n * 7}d" if unit == "w" else f"older_than:{value}"
    if key == "larger_than":
        b = size_bytes(value)
        return f"larger:{b // 1024**2}M" if b % 1024**2 == 0 else f"larger:{b // 1024}K"
    if key in LIST_KEYS:
        return _gmail_or(_gmail_items(key, value))
    if key == "category":
        return f"category:{value}"
    if key == "is_read":
        return "is:read" if value else "is:unread"
    if key == "replied":
        return None  # no Gmail operator; checked per thread instead
    if key == "any_of":
        return _gmail_or([_gmail_group(b) for b in value])
    raise ValueError(f"unknown condition {key}")


def _gmail_group(conds: dict) -> str:
    terms = [t for k, v in conds.items() if (t := _gmail_term(k, v))]
    return terms[0] if len(terms) == 1 else "(" + " ".join(terms) + ")"


def _gmail_plan(conds, excl, keep, defaults, extra, post, notes) -> dict:
    terms = ["in:inbox"] + [t for k, v in conds.items() if (t := _gmail_term(k, v))]
    for key, value in excl.items():  # skip the message if ANY exclusion matches
        if key in LIST_KEYS:
            terms += ["-" + t for t in _gmail_items(key, value)]
        else:
            terms.append("-" + _gmail_term(key, value))
    terms.append("-is:starred")
    terms += [f"-from:{from_needle(p)}" for p in keep]
    if extra:
        terms.append(extra)
    base = " ".join(terms)
    if "replied" in conds:
        notes.append(
            "Gmail can't search for replies: fetch each candidate's thread and pass "
            "replied=true if any message in it was sent by this account"
        )
    if defaults.get("exclude_important", True):
        return {"query": base + " -is:important", "query_without_important": base, "post_checks": post}
    return {"query": base, "post_checks": post}


# --- Microsoft Graph ------------------------------------------------------------

def _kql_term(key: str, value, today: date) -> str | None:
    if key == "older_than":
        return f"received<{cutoff(value, today).isoformat()}"
    if key == "larger_than":
        return f"size>{size_bytes(value)}"
    if key == "from":
        items = [f"from:{from_needle(p)}" for p in as_list(value)]
    elif key == "subject":
        items = [f"subject:{_quote(p)}" for p in as_list(value)]
    elif key == "contains":
        items = [_quote(p) for p in as_list(value)]
    elif key == "any_of":
        items = [_kql_group(b, today) for b in value]
    elif key in ("is_read", "replied"):
        return None  # checked on the results
    else:
        raise ValueError(f"unknown condition {key}")
    return items[0] if len(items) == 1 else "(" + " OR ".join(items) + ")"


def _kql_group(conds: dict, today: date) -> str:
    terms = [t for k, v in conds.items() if (t := _kql_term(k, v, today))]
    return terms[0] if len(terms) == 1 else "(" + " AND ".join(terms) + ")"


def _graph_plan(conds, excl, keep, defaults, extra, post, notes, today, batch) -> dict:
    select = "id,internetMessageId,subject,from,receivedDateTime,isRead,flag,importance"
    plan = {"folder": "inbox", "select": select, "top": batch, "post_checks": post}
    if "replied" in conds:
        plan["expand"] = "singleValueExtendedProperties($filter=id eq 'Integer 0x1081')"
        notes.append("replied = PidTagLastVerbExecuted (0x1081) is 102 (reply) or 103 (reply all)")
    if set(conds) <= {"older_than", "is_read", "replied"} and not excl:
        # everything fits an exact server-side $filter
        parts = []
        if "older_than" in conds:
            parts.append(f"receivedDateTime lt {cutoff(conds['older_than'], today).isoformat()}T00:00:00Z")
        if "is_read" in conds:
            parts.append(f"isRead eq {'true' if conds['is_read'] else 'false'}")
        parts.append("flag/flagStatus ne 'flagged'")
        if defaults.get("exclude_important", True):
            parts.append("importance ne 'high'")
        plan.update(mode="filter", filter=" and ".join(parts))
    else:
        # $search can't be combined with $filter on messages, so flags,
        # importance and read state are checked on the results instead
        terms = [t for k, v in conds.items() if (t := _kql_term(k, v, today))]
        for key, value in excl.items():
            t = _kql_term(key, value, today)
            if t:
                terms.append(f"NOT {t}")
        if extra:
            terms.append(extra)
        plan.update(mode="search", search=_quote(" AND ".join(terms)))
        if "is_read" in conds:
            post.append({"check": "is_read", "value": conds["is_read"]})
        if "is_read" in excl:
            post.append({"check": "is_read", "value": not excl["is_read"]})
        post.append({"check": "not_flagged"})
        if defaults.get("exclude_important", True):
            post.append({"check": "not_important"})
    return plan


# --- IMAP -----------------------------------------------------------------------

def _imap_or(terms: list[str]) -> str:
    """IMAP's OR takes exactly two keys, so n terms nest: OR a (OR b c)."""
    def nest(ts: list[str]) -> str:
        return ts[0] if len(ts) == 1 else f"OR {ts[0]} {'(' + nest(ts[1:]) + ')' if len(ts) > 2 else ts[1]}"
    return nest(terms) if len(terms) == 1 else "(" + nest(terms) + ")"


def _imap_term(key: str, value, today: date) -> str:
    if key == "older_than":
        return f"BEFORE {imap_date(cutoff(value, today))}"
    if key == "larger_than":
        return f"LARGER {size_bytes(value)}"
    if key == "from":
        return _imap_or([f"FROM {_quote(from_needle(p))}" for p in as_list(value)])
    if key == "subject":
        return _imap_or([f"SUBJECT {_quote(p)}" for p in as_list(value)])
    if key == "contains":
        return _imap_or([f"BODY {_quote(p)}" for p in as_list(value)])
    if key == "is_read":
        return "SEEN" if value else "UNSEEN"
    if key == "replied":
        return "ANSWERED" if value else "UNANSWERED"
    if key == "any_of":
        return _imap_or([_imap_group(b, today) for b in value])
    raise ValueError(f"unknown condition {key}")


def _imap_group(conds: dict, today: date) -> str:
    terms = [_imap_term(k, v, today) for k, v in conds.items()]
    return terms[0] if len(terms) == 1 else "(" + " ".join(terms) + ")"


def _imap_plan(conds, excl, keep, defaults, extra, post, notes, today) -> dict:
    terms = [_imap_term(k, v, today) for k, v in conds.items()]
    terms += [f"NOT {_imap_term(k, v, today)}" for k, v in excl.items()]
    terms.append("UNFLAGGED")
    # keep-senders are also checked exactly afterwards; excluding them in the
    # search stops them using up Yahoo's 1000-result cap
    terms += [f"NOT FROM {_quote(from_needle(p))}" for p in keep]
    if extra:
        terms.append(extra)
    if defaults.get("exclude_important", True):
        notes.append("IMAP has no 'important' marker; only starred/flagged is excluded")
    return {"folder": "INBOX", "criteria": " ".join(terms), "post_checks": post}


# --- entry points -----------------------------------------------------------------

def build_plan(rule: dict, family: str, provider: str, keep_senders: list[str],
               defaults: dict, today: date | None = None) -> dict:
    today = today or date.today()
    notes: list[str] = []
    base = {
        "rule": rule["name"],
        "target": rule["target"],
        "provider": provider,
        "family": family,
        "sample_size": defaults["sample_size"],
        "batch_size": defaults["batch_size"],
        "max_per_run": defaults["max_per_run"],
        "notes": notes,
    }
    opts = (rule.get("providers") or {}).get(provider) or {}
    if opts.get("note"):
        notes.append(opts["note"])
    if opts.get("skip"):
        return {**base, "skip": True, "reason": f"rule sets providers.{provider}.skip"}

    conds = _supported(rule["conditions"], family, notes)
    if conds is None:
        return {**base, "skip": True, "reason": notes[-1]}
    excl = rule.get("exclusions") or {}
    if UNSUPPORTED[family] & set(excl):
        # an exclusion we can't honour could move mail the rule meant to keep
        reason = f"skipped: exclusion uses {', '.join(sorted(UNSUPPORTED[family] & set(excl)))}, which this provider doesn't have"
        notes.append(reason)
        return {**base, "skip": True, "reason": reason}

    post: list[dict] = []
    if "from" in conds:
        post.append({"check": "from_matches", "patterns": as_list(conds["from"])})
    if keep_senders:
        post.append({"check": "not_keep_sender", "patterns": list(keep_senders)})
    if "subject" in conds and family != "gmail":
        # IMAP SUBJECT is a substring match and Graph KQL matches loosely, so
        # re-check as whole words. (Gmail's subject: operator already does.)
        post.append({"check": "subject_words", "phrases": as_list(conds["subject"])})
    if "from" in excl:
        post.append({"check": "not_from", "patterns": as_list(excl["from"])})
    if "subject" in excl:
        post.append({"check": "not_subject", "phrases": as_list(excl["subject"])})
    if "replied" in conds:
        post.append({"check": "replied", "value": conds["replied"]})

    extra = opts.get("query_extra")
    if family == "gmail":
        detail = _gmail_plan(conds, excl, keep_senders, defaults, extra, post, notes)
    elif family == "graph":
        detail = _graph_plan(conds, excl, keep_senders, defaults, extra, post, notes, today, defaults["batch_size"])
    elif family == "imap":
        detail = _imap_plan(conds, excl, keep_senders, defaults, extra, post, notes, today)
    else:
        raise ValueError(f"unknown search family {family!r}")
    return {**base, "skip": False, **detail}


def _address(value) -> str:
    if isinstance(value, dict):  # Graph: {"emailAddress": {"address": ...}}
        value = (value.get("emailAddress") or value).get("address", "")
    return email.utils.parseaddr(str(value or ""))[1].lower()


def normalize_candidate(c: dict, family: str | None = None) -> dict:
    """Accept our own keys or a raw Microsoft Graph message."""
    c = dict(c)
    if "isRead" in c and "is_read" not in c:
        c["is_read"] = c["isRead"]
    if isinstance(c.get("flag"), dict) and "flagged" not in c:
        c["flagged"] = c["flag"].get("flagStatus") == "flagged"
    if "importance" in c and "important" not in c:
        c["important"] = str(c["importance"]).lower() == "high"
    if family == "graph" and "replied" not in c:
        # The plan's $expand returns the 0x1081 property only when it is set;
        # a message that was never replied to or forwarded has no such entry.
        props = c.get("singleValueExtendedProperties") or []
        verbs = [p.get("value") for p in props if "0x1081" in str(p.get("id", "")).lower()]
        c["replied"] = any(str(v) in ("102", "103") for v in verbs)
    return c


def check_candidate(candidate: dict, post_checks: list[dict], family: str | None = None) -> tuple[bool, str]:
    """Apply a plan's post_checks to one message. Unknown values fail safe (skip)."""
    candidate = normalize_candidate(candidate, family)
    sender = _address(candidate.get("from"))
    subject = candidate.get("subject") or ""
    for chk in post_checks:
        kind = chk["check"]
        if kind == "from_matches":
            if not any(sender_matches(sender, p) for p in chk["patterns"]):
                return False, f"sender {sender or '?'} doesn't match {chk['patterns']}"
        elif kind in ("not_keep_sender", "not_from"):
            hit = next((p for p in chk["patterns"] if sender_matches(sender, p)), None)
            if hit:
                return False, f"sender {sender} matches {'keep-sender' if kind == 'not_keep_sender' else 'exclusion'} {hit}"
        elif kind == "subject_words":
            if not any(phrase_in(subject, p) for p in chk["phrases"]):
                return False, f"subject {subject!r} has none of {chk['phrases']} as a whole word"
        elif kind == "not_subject":
            if any(phrase_in(subject, p) for p in chk["phrases"]):
                return False, "subject matches an exclusion"
        elif kind == "not_flagged":
            if candidate.get("flagged") is not False:
                return False, "flagged" if candidate.get("flagged") else "flag state unknown"
        elif kind == "not_important":
            if candidate.get("important") is not False:
                return False, "important" if candidate.get("important") else "importance unknown"
        elif kind in ("is_read", "replied"):
            if candidate.get(kind) is None:
                return False, f"{kind} unknown"
            if bool(candidate[kind]) != chk["value"]:
                return False, f"{kind} is {candidate[kind]}"
        else:
            return False, f"unknown check {kind}"
    return True, ""
