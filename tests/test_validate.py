from pathlib import Path

import pytest
import validate_rules as v
from mcconfig import ROOT, provider_names, rule_files

PROVIDERS = provider_names()


def check(tmp_path, text, name="my-rule"):
    p = tmp_path / f"{name}.md"
    p.write_text(text)
    return v.validate_file(p, PROVIDERS)[1]


GOOD = "---\nname: my-rule\ndescription: d\ntarget: My_Target\nconditions:\n  older_than: 1y\n---\nwhy\n"


def test_shipped_rules_pass():
    assert rule_files()
    for p in rule_files():
        assert v.validate_file(p, PROVIDERS)[1] == [], p


def test_template_is_not_run_as_a_rule():
    assert all(not p.name.startswith("_") for p in rule_files())


def test_good_rule(tmp_path):
    assert check(tmp_path, GOOD) == []


@pytest.mark.parametrize("bad,expect", [
    (GOOD.replace("older_than: 1y", "older_than: 1 year"), "duration"),
    (GOOD.replace("older_than: 1y", "older_than: 1y\n  colour: red"), "unknown condition"),
    (GOOD.replace("My_Target", "Trash"), "system folder"),
    (GOOD.replace("My_Target", '"[Gmail]/Spam"'), "may only use"),
    (GOOD.replace("name: my-rule", "name: other"), "must match the filename"),
    (GOOD.replace("older_than: 1y", "is_read: true"), "at least one of"),
    (GOOD.replace("conditions:\n  older_than: 1y", "conditions:\n  from: [noreply@*]\n  category: sale"), "not one of"),
    (GOOD + "", "x") if False else (GOOD.replace("description: d\n", ""), "missing required key 'description'"),
    (GOOD.replace("---\nwhy", "extra: 1\n---\nwhy"), "unknown key"),
    (GOOD.replace("older_than: 1y", "older_than: 1y\n  larger_than: 5 parsecs"), "size"),
    (GOOD.replace("older_than: 1y", "older_than: 1y\n  from: [no spaces allowed]"), "address pattern"),
    (GOOD.replace("---\nwhy", "providers: {aol: {skip: true}}\n---\nwhy"), "unknown provider"),
    (GOOD.replace("---\nwhy", "exclusions:\n  any_of: [{from: [a@b.c]}, {from: [d@e.f]}]\n---\nwhy"), "only allowed directly under conditions"),
    (GOOD.replace("older_than: 1y", "older_than: 1y\n  any_of:\n    - replied: false\n    - is_read: true"), "only allowed directly under conditions"),
    (GOOD.replace("older_than: 1y", "older_than: 1y\n  from: [yes]"), "non-empty text"),
])
def test_bad_rules_fail_clearly(tmp_path, bad, expect):
    errors = check(tmp_path, bad)
    assert any(expect in e for e in errors), errors


def test_no_frontmatter(tmp_path):
    assert "no YAML frontmatter" in check(tmp_path, "just text")[0]
