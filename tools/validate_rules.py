#!/usr/bin/env python3
"""Check rule files against the mail-cleaner rule format.

    uv run tools/validate_rules.py                # every rules/*.md
    uv run tools/validate_rules.py rules/x.md     # just these files

Exit code 0 = all good, 1 = at least one error. Warnings don't fail.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcconfig import ROOT, parse_frontmatter, provider_names, rule_files  # noqa: E402

TOP_KEYS = {"name", "description", "target", "order", "conditions", "exclusions", "providers"}
REQUIRED = ("name", "description", "target", "conditions")
COND_KEYS = {"older_than", "larger_than", "from", "subject", "contains", "category", "is_read", "replied", "any_of"}
# a rule must narrow by at least one of these, so "is_read: true" alone can't sweep a whole Inbox
NARROWING = {"older_than", "larger_than", "from", "subject", "contains", "category"}
CATEGORIES = {"promotions", "social", "updates", "forums"}
PROVIDER_KEYS = {"skip", "query_extra", "note"}
RESERVED_TARGETS = {
    "inbox", "sent", "sent items", "sent mail", "drafts", "draft", "trash", "bin",
    "deleted", "deleted items", "deleted messages", "junk", "junk email", "spam", "bulk",
    "archive", "outbox", "starred", "important", "all mail", "unread", "chats",
    "snoozed", "scheduled", "conversation history",
}

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TARGET_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _-]{0,62}[A-Za-z0-9])?$")
DURATION_RE = re.compile(r"^[1-9]\d*[dwmy]$")
SIZE_RE = re.compile(r"^\d+(\.\d+)?(KB|MB|GB)$", re.IGNORECASE)


def _phrases(value, where: str, errors: list[str], is_sender: bool = False) -> None:
    items = value if isinstance(value, list) else [value]
    if not items:
        errors.append(f"{where}: list is empty")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where}: {item!r} must be a non-empty text value (quote it if YAML read it as yes/no/number)")
        elif is_sender and (" " in item.strip() or not ("@" in item or "." in item)):
            errors.append(f"{where}: {item!r} is not an address pattern like 'noreply@*', '@shop.example' or 'shop.example'")


def check_conditions(conds, where: str, errors: list[str], top_level: bool = True) -> None:
    """top_level=True only for the rule's own `conditions` mapping."""
    if not isinstance(conds, dict) or not conds:
        errors.append(f"{where}: must be a non-empty mapping of condition: value")
        return
    for key, val in conds.items():
        here = f"{where}.{key}"
        if key not in COND_KEYS:
            errors.append(f"{here}: unknown condition (allowed: {', '.join(sorted(COND_KEYS))})")
        elif key == "replied" and not top_level:
            # replies are checked on the results, so this can't sit inside an OR or an exclusion
            errors.append(f"{here}: 'replied' is only allowed directly under conditions (use replied: false there)")
        elif key == "older_than":
            if not (isinstance(val, str) and DURATION_RE.match(val)):
                errors.append(f"{here}: {val!r} is not a duration like 30d, 2w, 6m, 2y")
        elif key == "larger_than":
            if not (isinstance(val, str) and SIZE_RE.match(val.replace(" ", ""))):
                errors.append(f"{here}: {val!r} is not a size like 500KB, 5MB, 1GB")
        elif key in ("from", "subject", "contains"):
            _phrases(val, here, errors, is_sender=(key == "from"))
        elif key == "category":
            if val not in CATEGORIES:
                errors.append(f"{here}: {val!r} is not one of {', '.join(sorted(CATEGORIES))}")
        elif key in ("is_read", "replied"):
            if not isinstance(val, bool):
                errors.append(f"{here}: must be true or false")
        elif key == "any_of":
            if not top_level:
                errors.append(
                    f"{here}: any_of is only allowed directly under conditions "
                    "(exclusions already skip a message if ANY of them matches)"
                )
            elif not isinstance(val, list) or len(val) < 2:
                errors.append(f"{here}: needs a list of at least 2 condition groups")
            else:
                for i, branch in enumerate(val):
                    check_conditions(branch, f"{here}[{i}]", errors, top_level=False)


def narrows(conds: dict) -> bool:
    if NARROWING & set(conds):
        return True
    branches = conds.get("any_of")
    return isinstance(branches, list) and bool(branches) and all(
        isinstance(b, dict) and NARROWING & set(b) for b in branches
    )


def validate_file(path: Path, providers: list[str]) -> tuple[dict | None, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        data, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except Exception as exc:  # YAML errors, unclosed frontmatter
        return None, [f"can't read frontmatter: {exc}"], warnings
    if not data:
        return None, ["no YAML frontmatter (the file must start with ---)"], warnings

    for key in REQUIRED:
        if key not in data:
            errors.append(f"missing required key '{key}'")
    for key in set(data) - TOP_KEYS:
        errors.append(f"unknown key '{key}' (allowed: {', '.join(sorted(TOP_KEYS))})")

    name = data.get("name")
    if name is not None:
        if not (isinstance(name, str) and NAME_RE.match(name)):
            errors.append(f"name {name!r} must be kebab-case (letters, digits, dashes)")
        elif name != path.stem:
            errors.append(f"name '{name}' must match the filename '{path.stem}.md'")

    desc = data.get("description")
    if desc is not None and not (isinstance(desc, str) and desc.strip()):
        errors.append("description must be a non-empty line of text")

    target = data.get("target")
    if target is not None:
        if not (isinstance(target, str) and TARGET_RE.match(target)):
            errors.append(f"target {target!r} may only use letters, digits, space, _ and - (max 64 chars)")
        elif target.strip().lower() in RESERVED_TARGETS or target.lower().startswith("[gmail]"):
            errors.append(f"target '{target}' is a system folder/label; pick your own name")

    order = data.get("order", 100)
    if not (isinstance(order, int) and not isinstance(order, bool) and 1 <= order <= 999):
        errors.append(f"order {order!r} must be a whole number from 1 to 999")

    conds = data.get("conditions")
    if conds is not None:
        check_conditions(conds, "conditions", errors)
        if isinstance(conds, dict) and not narrows(conds):
            errors.append(
                "conditions must include at least one of "
                f"{', '.join(sorted(NARROWING))} so the rule can't sweep a whole Inbox"
            )

    excl = data.get("exclusions")
    if excl not in (None, {}):
        check_conditions(excl, "exclusions", errors, top_level=False)

    provs = data.get("providers")
    if provs not in (None, {}):
        if not isinstance(provs, dict):
            errors.append("providers must be a mapping like {yahoo: {skip: true}}")
        else:
            for prov, opts in provs.items():
                if prov not in providers:
                    errors.append(f"providers.{prov}: unknown provider (have: {', '.join(providers)})")
                if not isinstance(opts, dict):
                    errors.append(f"providers.{prov}: must be a mapping like {{skip: true}}")
                    continue
                for k, v in opts.items():
                    if k not in PROVIDER_KEYS:
                        errors.append(f"providers.{prov}.{k}: unknown option (allowed: {', '.join(sorted(PROVIDER_KEYS))})")
                    elif k == "skip" and not isinstance(v, bool):
                        errors.append(f"providers.{prov}.skip: must be true or false")
                    elif k in ("query_extra", "note") and not isinstance(v, str):
                        errors.append(f"providers.{prov}.{k}: must be text")

    if not body.strip():
        warnings.append("no explanation under the frontmatter; one sentence on why the rule exists helps reviewers")
    return data, errors, warnings


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or rule_files()
    providers = provider_names()
    failed = False
    seen_names: dict[str, Path] = {}
    seen_targets: dict[str, Path] = {}
    for path in paths:
        data, errors, warnings = validate_file(path, providers)
        if data:
            name, target = data.get("name"), str(data.get("target", "")).lower()
            if name in seen_names:
                errors.append(f"name '{name}' is already used by {seen_names[name]}")
            seen_names.setdefault(name, path)
            if target and target in seen_targets:
                warnings.append(f"target '{data['target']}' is shared with {seen_targets[target]}")
            seen_targets.setdefault(target, path)
        try:
            shown = path.resolve().relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"{'FAIL' if errors else 'OK  '}  {shown}")
        for e in errors:
            print(f"      error: {e}")
        for w in warnings:
            print(f"      warning: {w}")
        failed |= bool(errors)
    if not paths:
        print("no rule files found in rules/")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
