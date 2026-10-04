#!/usr/bin/env python3
"""PreToolUse guard for mail-cleaner's MCP servers (gmail, ms365, mailcleaner).

Blocks what a permissions deny-list can't express, because it depends on the
arguments of a call:
  * any tool whose name says delete / trash / send / forward / filter ...
  * Gmail labels: only action=create; never add the TRASH or SPAM label
  * moves into Trash / Deleted Items / Junk / Spam / Bulk
  * any call on a mailbox that config/accounts.md doesn't allow
    (real mailboxes need enabled=yes AND live=yes, which only the owner sets)

Stdlib only, so it runs on a bare python3. Fails closed for our servers.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

OUR_SERVERS = {"gmail", "ms365", "mailcleaner"}
PROVIDER_OF_SERVER = {"gmail": "gmail", "ms365": "microsoft"}

BLOCKED_WORDS = {
    "delete", "trash", "expunge", "purge", "empty", "send", "forward",
    "reply", "filter", "filters", "update", "logout",
}
BLOCKED_LABELS = {"TRASH", "SPAM"}
BLOCKED_FOLDERS = {
    "trash", "bin", "deleted", "deleted items", "deleted messages", "deleteditems",
    "junk", "junk email", "junkemail", "spam", "bulk", "bulk mail",
    "recoverableitemsdeletions", "[gmail]/trash", "[gmail]/spam",
}
FOLDER_KEYS = {"destinationid", "destination_id", "target", "target_folder", "folder"}
MS365_WRITE_TOOLS = {"create-mail-folder", "create-mail-child-folder", "move-mail-message", "create-draft-email"}


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"mail-cleaner guard: {reason}",
        }
    }))
    sys.exit(0)


def values_for(obj, keys: set[str]) -> list:
    """Every value stored under one of `keys` (case-insensitive), at any depth."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in keys:
                found.append(v)
            found.extend(values_for(v, keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(values_for(item, keys))
    return found


def is_blocked_folder(name) -> bool:
    if not isinstance(name, str):
        return False
    n = name.strip().lower()
    return n in BLOCKED_FOLDERS or n.endswith(("/trash", "/spam", "/junk", "/bin"))


def check_account(key: str | None, provider: str | None, accounts: list[dict]) -> None:
    from mcconfig import find_account, write_allowed

    if not key:
        deny("this call must name the mailbox (user_google_email / account)")
    acct = find_account(key, accounts)
    ok, why = write_allowed(acct)
    if not ok:
        deny(f"'{key}' is not allowed: {why}")
    if provider and acct["provider"] != provider:
        deny(f"'{key}' is a {acct['provider']} account, not {provider}")


def check(tool_name: str, args: dict) -> None:
    from mcconfig import load_accounts

    parts = tool_name.split("__")
    if len(parts) < 3 or parts[0] != "mcp" or parts[1] not in OUR_SERVERS:
        return  # not one of ours
    server, tool = parts[1], "__".join(parts[2:])
    words = set(re.split(r"[-_]", tool.lower()))

    if words & BLOCKED_WORDS or (server == "ms365" and "rule" in words):
        deny(f"'{tool}' can delete, send or change settings; mail-cleaner only moves and labels")

    for folder in values_for(args, FOLDER_KEYS):
        if is_blocked_folder(folder):
            deny(f"moving mail to '{folder}' is not allowed (that is how mail gets deleted)")

    accounts = load_accounts()

    if server == "gmail":
        if tool == "manage_gmail_label" and str(args.get("action", "")).lower() != "create":
            deny("Gmail labels may only be created, never updated or deleted")
        for labels in values_for(args, {"add_label_ids"}):
            if isinstance(labels, str):
                labels = [labels]
            hit = {str(l).upper() for l in labels or []} & BLOCKED_LABELS
            if hit:
                deny(f"adding {sorted(hit)} would trash/spam the mail")
        check_account(args.get("user_google_email"), "gmail", accounts)

    elif server == "ms365":
        if args.get("account"):
            check_account(str(args["account"]), "microsoft", accounts)
        elif tool in MS365_WRITE_TOOLS:
            # Single-account mode has no 'account' argument, so we can't see which
            # mailbox is signed in. Only allow writes when every enabled Microsoft
            # account in config is allowed, and at least one is.
            from mcconfig import write_allowed

            ms = [a for a in accounts if a["provider"] == "microsoft" and a["enabled"]]
            if not ms or not all(write_allowed(a)[0] for a in ms):
                deny(
                    "Microsoft writes need every enabled microsoft account in "
                    "config/accounts.md to be allowed (test, or real with live=yes)"
                )

    elif server == "mailcleaner":
        if "account" in args:
            check_account(str(args.get("account") or ""), None, accounts)
        for acct in args.get("accounts") or []:
            check_account(str(acct), None, accounts)


def main() -> None:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
        check(event.get("tool_name", ""), event.get("tool_input") or {})
    except SystemExit:
        raise
    except Exception as exc:  # fail closed, but only for our own servers
        if re.search(r"mcp__(gmail|ms365|mailcleaner)__", raw):
            print(f"mail-cleaner guard error, blocking to be safe: {exc!r}", file=sys.stderr)
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
