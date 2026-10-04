import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"

ACCOUNTS = """| id | provider | address | kind | enabled | live |
|---|---|---|---|---|---|
| g-test | gmail | t@gmail.com | test | yes | no |
| g-real | gmail | me@gmail.com | real | yes | no |
| g-live | gmail | live@gmail.com | real | yes | yes |
| g-off | gmail | off@gmail.com | test | no | no |
| o-test | microsoft | t@outlook.com | test | yes | no |
| y-test | yahoo | t@yahoo.com | test | yes | no |
"""


@pytest.fixture
def accounts(tmp_path):
    f = tmp_path / "accounts.md"
    f.write_text(ACCOUNTS)
    return f


def run(tool, args, accounts):
    import os
    env = {**os.environ, "MAILCLEANER_ACCOUNTS_FILE": str(accounts)}
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_name": tool, "tool_input": args}),
                       capture_output=True, text=True, env=env)
    if p.returncode == 2:
        return "deny"
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


G = "mcp__gmail__"


@pytest.mark.parametrize("tool,args,want", [
    (G + "search_gmail_messages", {"query": "x", "user_google_email": "t@gmail.com"}, "allow"),
    (G + "batch_modify_gmail_message_labels", {"user_google_email": "t@gmail.com", "message_ids": ["1"], "add_label_ids": ["Label_1"], "remove_label_ids": ["INBOX"]}, "allow"),
    (G + "batch_modify_gmail_message_labels", {"user_google_email": "t@gmail.com", "message_ids": ["1"], "add_label_ids": ["TRASH"]}, "deny"),
    (G + "batch_modify_gmail_message_labels", {"user_google_email": "t@gmail.com", "message_ids": ["1"], "add_label_ids": ["spam"]}, "deny"),
    (G + "manage_gmail_label", {"user_google_email": "t@gmail.com", "action": "create", "name": "Dump1"}, "allow"),
    (G + "manage_gmail_label", {"user_google_email": "t@gmail.com", "action": "delete", "label_id": "x"}, "deny"),
    (G + "manage_gmail_label", {"user_google_email": "t@gmail.com", "action": "update", "label_id": "x"}, "deny"),
    (G + "send_gmail_message", {"user_google_email": "t@gmail.com"}, "deny"),
    (G + "manage_gmail_filter", {"user_google_email": "t@gmail.com"}, "deny"),
    (G + "search_gmail_messages", {"query": "x", "user_google_email": "me@gmail.com"}, "deny"),   # real, live=no
    (G + "search_gmail_messages", {"query": "x", "user_google_email": "live@gmail.com"}, "allow"),  # real, live=yes
    (G + "search_gmail_messages", {"query": "x", "user_google_email": "off@gmail.com"}, "deny"),    # disabled
    (G + "search_gmail_messages", {"query": "x", "user_google_email": "nobody@gmail.com"}, "deny"),  # unlisted
    (G + "search_gmail_messages", {"query": "x"}, "deny"),                                           # no account
    (G + "search_gmail_messages", {"query": "x", "user_google_email": "T@GMAIL.COM"}, "allow"),
    ("mcp__ms365__move-mail-message", {"message-id": "1", "body": {"destinationId": "AAMk"}}, "allow"),
    ("mcp__ms365__move-mail-message", {"message-id": "1", "body": {"destinationId": "deleteditems"}}, "deny"),
    ("mcp__ms365__move-mail-message", {"message-id": "1", "body": {"DestinationId": "JunkEmail"}}, "deny"),
    ("mcp__ms365__delete-mail-message", {"message-id": "1"}, "deny"),
    ("mcp__ms365__send-mail", {}, "deny"),
    ("mcp__ms365__create-mail-rule", {}, "deny"),
    ("mcp__ms365__list-mail-folder-messages", {"mailFolder-id": "inbox"}, "allow"),
    ("mcp__mailcleaner__imap_search", {"account": "y-test", "rule": "dump1-big-files"}, "allow"),
    ("mcp__mailcleaner__imap_search", {"account": "g-real", "rule": "x"}, "deny"),
    ("mcp__mailcleaner__imap_move", {"account": "nobody", "rule": "x"}, "deny"),
    ("mcp__mailcleaner__start_run", {"mode": "apply", "accounts": ["y-test", "g-real"], "rules": []}, "deny"),
    ("mcp__mailcleaner__start_run", {"mode": "apply", "accounts": ["y-test"], "rules": []}, "allow"),
    ("mcp__someone_else__delete_everything", {}, "allow"),  # not our server
    ("Bash", {"command": "ls"}, "allow"),
])
def test_guard(tool, args, want, accounts):
    assert run(tool, args, accounts) == want


def test_ms365_write_blocked_when_any_microsoft_account_is_real_and_not_live(tmp_path):
    f = tmp_path / "a.md"
    f.write_text(ACCOUNTS + "| o-real | microsoft | me@outlook.com | real | yes | no |\n")
    assert run("mcp__ms365__move-mail-message", {"message-id": "1", "body": {"destinationId": "x"}}, f) == "deny"
    assert run("mcp__ms365__list-mail-folders", {}, f) == "allow"


def test_guard_fails_closed_on_garbage_for_our_servers(accounts):
    import os
    env = {**os.environ, "MAILCLEANER_ACCOUNTS_FILE": str(accounts)}
    p = subprocess.run([sys.executable, str(GUARD)], input='{"tool_name": "mcp__gmail__search_gmail_messages", "tool_input": "oops"', capture_output=True, text=True, env=env)
    assert p.returncode == 2
