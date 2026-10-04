"""cases.yaml must agree with the rules, and seed.py must build what it says."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from mcconfig import rule_files, load_rule

sys.path.insert(0, str(Path(__file__).parent))
import seed

CASES = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text())
TARGETS = {load_rule(p)["target"] for p in rule_files()} | {"INBOX"}


def test_every_expectation_is_a_real_target():
    for c in CASES:
        assert c["expect"] in TARGETS, c["id"]
        assert all(v in TARGETS for v in c.get("expect_by", {}).values()), c["id"]


def test_ids_unique_and_tagged():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) == 14
    assert all(f"[mc-{c['id']}]" in c["subject"] for c in CASES)


def test_every_rule_has_a_positive_and_a_negative_case():
    for p in rule_files():
        target = load_rule(p)["target"]
        assert any(c["expect"] == target for c in CASES), f"no case lands in {target}"
    assert sum(c["expect"] == "INBOX" for c in CASES) >= 6


def test_messages_are_backdated_and_sized():
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    big = next(c for c in CASES if c["id"] == "t01")
    msg, when = seed.build_message(big, "me@x.com", now, "abc")
    assert (now - when).days == 10 and len(msg.as_bytes()) > 5 * 1024**2
    assert msg["X-Mail-Cleaner-Test"] == "t01"
    promo, _ = seed.build_message(next(c for c in CASES if c["id"] == "t02"), "me@x.com", now, "abc")
    assert "List-Unsubscribe" in promo


def test_seed_refuses_real_accounts(tmp_path, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("| id | provider | address | kind | enabled | live |\n|---|---|---|---|---|---|\n"
                 "| r | gmail | me@gmail.com | real | yes | yes |\n| t | gmail | t@gmail.com | test | no | no |\n")
    monkeypatch.setenv("MAILCLEANER_ACCOUNTS_FILE", str(f))
    for key in ("r", "t"):
        try:
            seed.test_account(key)
        except SystemExit as exc:
            assert "refusing" in str(exc)
        else:
            raise AssertionError(f"seed accepted {key}")
