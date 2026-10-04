#!/usr/bin/env python3
"""Seed and verify the live-test messages on a TEST account.

    uv run --group seed tests/seed.py seed    gmail-test
    uv run --group seed tests/seed.py verify  gmail-test
    uv run --group seed tests/seed.py cleanup gmail-test      (gmail only: removes the seeded messages)
    uv run tests/seed.py seed yahoo-test        (IMAP APPEND, needs the app password in .env)
    uv run tests/seed.py seed outlook-test      (writes tests/out/outlook-payloads.json for Claude to post)

Refuses any account that isn't kind: test and enabled: yes in config/accounts.md.
Messages are real-looking mail with backdated Date headers (Gmail: messages.insert
with internalDateSource=dateHeader; Yahoo: IMAP APPEND with a backdated INTERNALDATE).
"""

from __future__ import annotations

import base64
import email.utils
import imaplib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "tools")]
import mcconfig  # noqa: E402

TOKENS = HERE / ".tokens"
OUT = HERE / "out"
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def load_cases() -> list[dict]:
    import yaml

    return yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))


def test_account(key: str) -> dict:
    acct = mcconfig.find_account(key)
    if not acct:
        sys.exit(f"'{key}' is not in config/accounts.md")
    if acct["kind"] != "test" or not acct["enabled"]:
        sys.exit(f"refusing: '{key}' must be kind: test and enabled: yes (it is kind={acct['kind']}, enabled={acct['enabled']})")
    return acct


def build_message(case: dict, to: str, now: datetime, seed_id: str) -> tuple[EmailMessage, datetime]:
    when = now - timedelta(days=case["age_days"])
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = case["from"], to, case["subject"]
    msg["Date"] = email.utils.format_datetime(when)
    msg["Message-ID"] = f"<{case['id']}.{seed_id}@mail-cleaner.test>"
    msg["X-Mail-Cleaner-Test"] = case["id"]
    if "unsubscribe" in case.get("body", "").lower():
        msg["List-Unsubscribe"] = "<mailto:unsubscribe@shop.example>"
    msg.set_content(case.get("body") or f"Test message {case['id']}: {case['why']}.")
    if case.get("size_mb"):
        # random bytes don't compress, so the message really is about size_mb in size
        payload = os.urandom(int(case["size_mb"] * 1024 * 1024 * 0.75))
        msg.add_attachment(payload, maintype="application", subtype="octet-stream", filename="payload.bin")
    return msg, when


# --- Gmail ----------------------------------------------------------------

def gmail_service(acct: dict):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    mcconfig.load_dotenv()
    cid, secret = os.environ.get("GOOGLE_OAUTH_CLIENT_ID"), os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not (cid and secret):
        sys.exit("set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env first (see tests/accounts-setup.md)")
    TOKENS.mkdir(exist_ok=True)
    token_file = TOKENS / f"{acct['id']}.json"
    creds = Credentials.from_authorized_user_file(str(token_file), GMAIL_SCOPES) if token_file.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_config(
            {"installed": {"client_id": cid, "client_secret": secret, "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                           "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}},
            GMAIL_SCOPES)
        print(f"A browser window opens: sign in as {acct['address']} (the TEST account) and allow access.")
        creds = flow.run_local_server(port=0, login_hint=acct["address"])  # pre-selects the test account
    token_file.write_text(creds.to_json())
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    who = svc.users().getProfile(userId="me").execute()["emailAddress"].lower()
    if who != acct["address"]:
        token_file.unlink(missing_ok=True)
        sys.exit(f"signed in as {who}, but config/accounts.md says {acct['address']}. Nothing was touched; token discarded.")
    return svc


def gmail_seed(acct: dict) -> None:
    from googleapiclient.http import MediaIoBaseUpload
    import io

    svc, now, seed_id = gmail_service(acct), datetime.now(timezone.utc), uuid.uuid4().hex[:8]
    seeded = {}
    for case in load_cases():
        flags = case.get("flags", [])
        labels = ["INBOX"] + ([] if "read" in flags else ["UNREAD"]) + (["STARRED"] if "starred" in flags else []) \
            + (["IMPORTANT"] if "important" in flags else []) \
            + ([f"CATEGORY_{case['gmail_category'].upper()}"] if case.get("gmail_category") else [])
        msg, _ = build_message(case, acct["address"], now, seed_id)
        raw = msg.as_bytes()
        media = MediaIoBaseUpload(io.BytesIO(raw), mimetype="message/rfc822", resumable=len(raw) > 4_000_000)
        res = svc.users().messages().insert(userId="me", internalDateSource="dateHeader", media_body=media,
                                            body={"labelIds": labels}).execute()
        seeded[case["id"]] = {"id": res["id"], "threadId": res["threadId"], "message_id": msg["Message-ID"]}
        if "replied" in flags:  # a sent reply in the same thread is what "replied" means in Gmail
            reply = EmailMessage()
            reply["From"], reply["To"], reply["Subject"] = acct["address"], case["from"], "Re: " + case["subject"]
            reply["Date"] = email.utils.format_datetime(now - timedelta(days=case["age_days"] - 1))
            reply["In-Reply-To"] = reply["References"] = msg["Message-ID"]
            reply["Message-ID"] = f"<{case['id']}.reply.{seed_id}@mail-cleaner.test>"
            reply.set_content("Sounds good, see you then.")
            svc.users().messages().insert(userId="me", internalDateSource="dateHeader",
                                          body={"labelIds": ["SENT"], "threadId": res["threadId"],
                                                "raw": base64.urlsafe_b64encode(reply.as_bytes()).decode()}).execute()
        print(f"  {case['id']}  {case['subject']}")
    (TOKENS / f"seeded-{acct['id']}.json").write_text(json.dumps(seeded, indent=2))
    print(f"seeded {len(seeded)} messages into {acct['address']}")


def gmail_verify(acct: dict) -> int:
    svc = gmail_service(acct)
    seeded = json.loads((TOKENS / f"seeded-{acct['id']}.json").read_text())
    names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    bad = 0
    for case in load_cases():
        want = case.get("expect_by", {}).get("gmail", case["expect"])
        got = "?"
        try:
            m = svc.users().messages().get(userId="me", id=seeded[case["id"]]["id"], format="minimal").execute()
            ids = m.get("labelIds", [])
            user = [names[i] for i in ids if i in names and i.startswith("Label_")]
            got = "INBOX" if "INBOX" in ids and not user else (user[0] if user else "(archived, no label)")
            if "TRASH" in ids or "SPAM" in ids:
                got = "!! TRASH/SPAM"
        except Exception as exc:
            got = f"missing ({type(exc).__name__})"
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {case['id']}  expected {want:14} got {got}")
    print("ALL AS EXPECTED" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


def gmail_cleanup(acct: dict) -> None:
    svc = gmail_service(acct)
    seeded = json.loads((TOKENS / f"seeded-{acct['id']}.json").read_text())
    ids = [v["id"] for v in seeded.values()]
    # test account only; labels are removed, the messages stay (we never delete)
    print("cleanup only strips the TEST account's INBOX/labels from seeded messages; nothing is deleted.")
    names = {l["id"]: l["name"] for l in svc.users().labels().list(userId="me").execute()["labels"]}
    extra = [i for i, n in names.items() if n in {"Dump1", "Dump2_Promos", "Dump3_Automated", "Dump4_OTP", "Archive_Old"}]
    svc.users().messages().batchModify(userId="me", body={"ids": ids, "removeLabelIds": extra, "addLabelIds": ["INBOX"]}).execute()
    print(f"reset {len(ids)} messages to the Inbox")


# --- Yahoo (IMAP APPEND) -----------------------------------------------------

def imap_session(acct: dict) -> imaplib.IMAP4_SSL:
    mcconfig.load_dotenv()
    prov = mcconfig.load_provider(acct["provider"])
    var = mcconfig.env_name(acct["id"], "APP_PASSWORD")
    if not os.environ.get(var):
        sys.exit(f"set {var} in .env (app password for {acct['address']}; see tests/accounts-setup.md)")
    conn = imaplib.IMAP4_SSL(prov["imap_host"], int(prov.get("imap_port", 993)))
    conn.login(acct["address"], os.environ[var])
    return conn


def imap_seed(acct: dict) -> None:
    conn, now, seed_id = imap_session(acct), datetime.now(timezone.utc), uuid.uuid4().hex[:8]
    for case in load_cases():
        flags = case.get("flags", [])
        imap_flags = " ".join(f for f, on in (("\\Seen", "read" in flags), ("\\Flagged", "starred" in flags),
                                              ("\\Answered", "replied" in flags)) if on)
        msg, when = build_message(case, acct["address"], now, seed_id)
        typ, _ = conn.append("INBOX", f"({imap_flags})", imaplib.Time2Internaldate(when.timestamp()), msg.as_bytes())
        print(f"  {case['id']}  {case['subject']}  {typ}")


def imap_verify(acct: dict) -> int:
    conn, bad = imap_session(acct), 0
    folders = [l.decode().rsplit(' "/" ', 1)[-1].strip('"') for l in conn.list()[1] if l]
    folders = [f for f in folders if f.lower() not in ("trash", "bulk", "bulk mail")] or ["INBOX"]
    for case in load_cases():
        want = case.get("expect_by", {}).get(acct["provider"], case["expect"])
        found = None
        for folder in folders:
            if conn.select(f'"{folder}"', readonly=True)[0] != "OK":
                continue
            typ, data = conn.uid("SEARCH", "HEADER", "X-Mail-Cleaner-Test", case["id"])
            if typ == "OK" and data and data[0]:
                found = folder
                break
        got = found or "missing"
        ok = got == want
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {case['id']}  expected {want:14} got {got}")
    print("ALL AS EXPECTED" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


# --- Outlook -----------------------------------------------------------------

def outlook_seed(acct: dict) -> None:
    """Outlook.com has no IMAP for apps any more, so Claude posts these through the
    ms365 MCP server (see tests/README.md). Payloads follow Microsoft Graph:
    PidTagMessageFlags 0x0E07 = 1 makes it a received (non-draft) message, and
    PidTagMessageDeliveryTime 0x0E06 / PidTagClientSubmitTime 0x0039 backdate it."""
    OUT.mkdir(exist_ok=True)
    now, seed_id, payloads = datetime.now(timezone.utc), uuid.uuid4().hex[:8], []
    for case in load_cases():
        flags = case.get("flags", [])
        msg, when = build_message(case, acct["address"], now, seed_id)
        iso = when.strftime("%Y-%m-%dT%H:%M:%SZ")
        props = [
            {"id": "Integer 0x0E07", "value": "1"},  # received, not a draft
            {"id": "SystemTime 0x0E06", "value": iso},
            {"id": "SystemTime 0x0039", "value": iso},
        ]
        if "replied" in flags:
            props.append({"id": "Integer 0x1081", "value": "102"})
        body = {
            "subject": case["subject"], "importance": "high" if "important" in flags else "normal", "isRead": "read" in flags,
            "from": {"emailAddress": dict(zip(("name", "address"), (email.utils.parseaddr(case["from"])))) },
            "toRecipients": [{"emailAddress": {"address": acct["address"]}}],
            "body": {"contentType": "text", "content": case.get("body") or f"Test message {case['id']}: {case['why']}."},
            "internetMessageHeaders": [{"name": "X-Mail-Cleaner-Test", "value": case["id"]}],
            "singleValueExtendedProperties": props,
        }
        if "starred" in flags:
            body["flag"] = {"flagStatus": "flagged"}
        payloads.append({"case": case["id"], "size_mb": case.get("size_mb"), "message": body})
    (OUT / "outlook-payloads.json").write_text(json.dumps(payloads, indent=2))
    print(f"wrote {OUT / 'outlook-payloads.json'} ({len(payloads)} messages). Next: ask Claude to post them (tests/README.md, step 5).")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("seed", "verify", "cleanup"):
        sys.exit(__doc__)
    action, acct = sys.argv[1], test_account(sys.argv[2])
    table = {
        "gmail": {"seed": gmail_seed, "verify": gmail_verify, "cleanup": gmail_cleanup},
        "yahoo": {"seed": imap_seed, "verify": imap_verify},
        "microsoft": {"seed": outlook_seed},
    }
    fn = table.get(acct["provider"], {}).get(action)
    if not fn:
        sys.exit(f"'{action}' isn't supported for {acct['provider']} here (Outlook: Claude seeds and verifies through ms365; see tests/README.md)")
    sys.exit(fn(acct) or 0)


if __name__ == "__main__":
    main()
