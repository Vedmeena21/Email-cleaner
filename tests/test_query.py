from datetime import date

import pytest
from mcconfig import sender_matches
from query import build_plan, check_candidate, duration_days, from_needle, phrase_in, size_bytes

TODAY = date(2026, 10, 3)
DEFAULTS = {"sample_size": 10, "batch_size": 100, "max_per_run": 500, "exclude_important": True}


def plan(conds, family, excl=None, keep=(), extra_rule=None):
    rule = {"name": "r", "target": "T", "conditions": conds, "exclusions": excl or {}, **(extra_rule or {})}
    return build_plan(rule, family, {"gmail": "gmail", "graph": "microsoft", "imap": "yahoo"}[family], list(keep), DEFAULTS, TODAY)


def test_units():
    assert duration_days("2y") == 730 and duration_days("6m") == 180 and duration_days("2w") == 14
    assert size_bytes("5MB") == 5 * 1024**2


def test_gmail_query():
    q = plan({"larger_than": "5MB", "older_than": "2y"}, "gmail", keep=["a@b.com"])["query"]
    assert q == "in:inbox older_than:2y larger:5M -is:starred -from:a@b.com -is:important".replace("older_than:2y larger:5M", "larger:5M older_than:2y")


def test_gmail_important_toggle():
    p = plan({"older_than": "1y"}, "gmail")
    assert "-is:important" in p["query"] and "-is:important" not in p["query_without_important"]


def test_gmail_any_of_and_replied_note():
    p = plan({"older_than": "1y", "replied": False, "any_of": [{"category": "promotions"}, {"contains": ["unsubscribe"]}]}, "gmail")
    assert '{category:promotions "unsubscribe"}' in p["query"]
    assert any("thread" in n for n in p["notes"]) and {"check": "replied", "value": False} in p["post_checks"]


def test_gmail_exclusions_negate():
    q = plan({"older_than": "1y"}, "gmail", excl={"from": ["x@y.com"], "category": "social"})["query"]
    assert "-from:x@y.com" in q and "-category:social" in q


def test_graph_filter_vs_search():
    f = plan({"older_than": "3y", "is_read": True, "replied": False}, "graph")
    assert f["mode"] == "filter" and "isRead eq true" in f["filter"] and "importance ne 'high'" in f["filter"]
    s = plan({"larger_than": "5MB"}, "graph")
    assert s["mode"] == "search" and s["search"] == '"size>5242880"'
    assert {"check": "not_flagged"} in s["post_checks"] and {"check": "not_important"} in s["post_checks"]


def test_category_is_skipped_or_dropped_without_provider_support():
    p = plan({"category": "promotions", "older_than": "1y"}, "imap")
    assert p["skip"] and "category" in p["reason"]
    p = plan({"older_than": "2y", "any_of": [{"category": "promotions"}, {"contains": ["unsubscribe"]}]}, "imap")
    assert not p["skip"] and 'BODY "unsubscribe"' in p["criteria"] and "category" not in p["criteria"]
    p = plan({"older_than": "1y"}, "imap", excl={"category": "social"})
    assert p["skip"], "an exclusion we can't honour must skip the rule, not be ignored"


def test_provider_skip_option():
    assert plan({"older_than": "1y"}, "imap", extra_rule={"providers": {"yahoo": {"skip": True}}})["skip"]


def test_imap_criteria():
    c = plan({"older_than": "30d", "subject": ["OTP", "login code"], "is_read": True, "replied": False}, "imap", keep=["k@x.com"])["criteria"]
    assert c == 'BEFORE 03-Sep-2026 (OR SUBJECT "OTP" SUBJECT "login code") SEEN UNANSWERED UNFLAGGED NOT FROM "k@x.com"'


def test_imap_quotes_are_escaped():
    assert r'\"' in plan({"older_than": "1y", "contains": ['say "hi"']}, "imap")["criteria"]


def test_from_needle_and_sender_match():
    assert from_needle("noreply@*") == "noreply@" and from_needle("@shop.example") == "shop.example"
    assert from_needle("*@shop.example") == "shop.example" and from_needle("a@b.com") == "a@b.com"
    assert sender_matches("noreply@x.com", "noreply@*") and not sender_matches("my-noreply@x.com", "noreply@*")
    assert sender_matches("a@mail.shop.example", "shop.example") and not sender_matches("a@notshop.example", "shop.example")


def test_whole_word_subjects():
    assert phrase_in("Your OTP is 123", "OTP") and not phrase_in("hotpot night", "OTP")
    assert phrase_in("Login Code: 55", "login code")


def test_check_candidate():
    pc = [{"check": "from_matches", "patterns": ["noreply@*"]}, {"check": "not_keep_sender", "patterns": ["noreply@keep.com"]}]
    assert check_candidate({"from": "A <noreply@x.com>"}, pc)[0]
    assert not check_candidate({"from": "noreply@keep.com"}, pc)[0]
    assert not check_candidate({"from": "friend@x.com"}, pc)[0]


@pytest.mark.parametrize("cand", [{}, {"flagged": None}, {"flagged": True}])
def test_unknown_or_true_flag_is_skipped(cand):
    assert not check_candidate(cand, [{"check": "not_flagged"}])[0]


def test_graph_message_normalised():
    msg = {"isRead": True, "flag": {"flagStatus": "notFlagged"}, "importance": "normal", "from": {"emailAddress": {"address": "a@b.com"}}}
    pc = [{"check": "is_read", "value": True}, {"check": "not_flagged"}, {"check": "not_important"}, {"check": "replied", "value": False}]
    assert check_candidate(msg, pc, "graph")[0]
    msg["singleValueExtendedProperties"] = [{"id": "Integer {00062008-0000-0000-C000-000000000046} Id 0x1081", "value": "102"}]
    assert not check_candidate(msg, pc, "graph")[0]
    assert not check_candidate({**msg, "importance": "high"}, pc, "graph")[0]


def test_whole_word_subject_check_runs_on_graph_and_imap_not_gmail():
    for fam, want in (("graph", True), ("imap", True), ("gmail", False)):
        checks = [c["check"] for c in plan({"older_than": "30d", "subject": ["OTP"]}, fam)["post_checks"]]
        assert ("subject_words" in checks) is want, fam
