"""Read mail-cleaner's markdown config files.

Shared by the guard hook, the mailcleaner MCP server and the rule validator.
Only rule/provider/default frontmatter needs PyYAML, and it is imported lazily,
so the hook can run on a bare `python3` without any packages installed.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
RULES = ROOT / "rules"
PROVIDERS = ROOT / "providers"
LOGS = ROOT / "logs"

ACCOUNT_COLUMNS = ("id", "provider", "address", "kind", "enabled", "live")


# --- markdown helpers -------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split '---\\nyaml\\n---\\nbody' into (dict, body). No frontmatter -> ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            import yaml  # lazy: the hook never needs it

            data = yaml.safe_load("".join(lines[1:i])) or {}
            if not isinstance(data, dict):
                raise ValueError("frontmatter must be a YAML mapping")
            return data, "".join(lines[i + 1 :])
    raise ValueError("frontmatter opened with '---' but never closed")


def _table_rows(text: str) -> list[dict]:
    """Rows of the first markdown table whose header has every ACCOUNT_COLUMNS name."""
    header: list[str] | None = None
    rows: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            if header is not None and rows:
                break  # table ended
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if header is None:
            lowered = [c.lower() for c in cells]
            if all(col in lowered for col in ACCOUNT_COLUMNS):
                header = lowered
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # the |---|---| separator
        rows.append(dict(zip(header, cells)))
    return rows


# --- accounts ---------------------------------------------------------------

def load_accounts(path: Path | None = None) -> list[dict]:
    path = Path(os.environ.get("MAILCLEANER_ACCOUNTS_FILE") or path or CONFIG / "accounts.md")
    accounts = []
    for row in _table_rows(path.read_text(encoding="utf-8")):
        acct = {k: row.get(k, "").strip() for k in ACCOUNT_COLUMNS}
        acct["provider"] = acct["provider"].lower()
        acct["address"] = acct["address"].lower()
        acct["kind"] = acct["kind"].lower()
        acct["enabled"] = acct["enabled"].lower() in ("yes", "true", "y")
        acct["live"] = acct["live"].lower() in ("yes", "true", "y")
        if acct["id"]:
            accounts.append(acct)
    return accounts


def find_account(key: str, accounts: list[dict] | None = None) -> dict | None:
    """Look an account up by id or by address (case-insensitive)."""
    key = (key or "").strip().lower()
    for acct in accounts if accounts is not None else load_accounts():
        if key in (acct["id"].lower(), acct["address"]):
            return acct
    return None


def write_allowed(acct: dict | None) -> tuple[bool, str]:
    """May mail-cleaner touch this mailbox at all (read or write)?

    test accounts: yes, when enabled. real accounts: only when enabled AND live.
    """
    if acct is None:
        return False, "not listed in config/accounts.md"
    if not acct["enabled"]:
        return False, f"account '{acct['id']}' has enabled=no in config/accounts.md"
    if acct["kind"] == "test":
        return True, "test account"
    if acct["kind"] == "real" and acct["live"]:
        return True, "real account with live=yes"
    if acct["kind"] == "real":
        return False, (
            f"account '{acct['id']}' is a real mailbox and live=no; only the owner "
            "may set live=yes in config/accounts.md"
        )
    return False, f"account '{acct['id']}' has unknown kind '{acct['kind']}' (use test or real)"


def env_name(account_id: str, suffix: str) -> str:
    """'yahoo-test', 'APP_PASSWORD' -> 'MC_YAHOO_TEST_APP_PASSWORD'."""
    return "MC_" + re.sub(r"[^A-Z0-9]", "_", account_id.upper()) + "_" + suffix


# --- keep-senders -----------------------------------------------------------

def load_keep_senders(path: Path | None = None) -> list[str]:
    """Bullet items under the '## Keep' heading of config/keep-senders.md."""
    path = path or CONFIG / "keep-senders.md"
    out, in_list = [], False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            in_list = line.lstrip("#").strip().lower() == "keep"
            continue
        if in_list and line.startswith(("- ", "* ")):
            item = line[2:].split("#", 1)[0].strip().strip("`").strip().lower()
            if item and " " not in item:
                out.append(item)
    return out


def sender_matches(address: str, pattern: str) -> bool:
    """Does a sender address match a keep-sender / rule `from` pattern?

    'a@b.com'      exact address (wildcards allowed: 'noreply@*')
    '@b.com', '*@b.com', 'b.com'   anyone at b.com or a subdomain of it
    """
    address, pattern = (address or "").strip().lower(), pattern.strip().lower()
    if not address:
        return False
    if pattern.startswith("*@"):
        pattern = pattern[1:]
    if pattern.startswith("@") or "@" not in pattern:
        domain = pattern.lstrip("@")
        return address.endswith("@" + domain) or address.endswith("." + domain)
    return fnmatch.fnmatchcase(address, pattern)


# --- rules, providers, defaults ---------------------------------------------

def rule_files(rules_dir: Path | None = None) -> list[Path]:
    """Rule files in filename order, skipping _TEMPLATE and friends."""
    rules_dir = rules_dir or RULES
    return sorted(p for p in rules_dir.glob("*.md") if not p.name.startswith(("_", ".")) and p.name != "README.md")


def rules_in_run_order(rules_dir: Path | None = None) -> list[dict]:
    """All rules sorted by (order, filename). The first rule to match a message wins."""
    rules = [load_rule(p) for p in rule_files(rules_dir)]
    return sorted(rules, key=lambda r: (int(r.get("order", 100)), str(r.get("name", ""))))


def load_rule(name_or_path: str | Path) -> dict:
    path = Path(name_or_path)
    if path.suffix != ".md":
        path = RULES / f"{name_or_path}.md"
    data, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return data


def provider_names(providers_dir: Path | None = None) -> list[str]:
    providers_dir = providers_dir or PROVIDERS
    return sorted(p.stem for p in providers_dir.glob("*.md") if not p.name.startswith(("_", ".")))


def load_provider(name: str) -> dict:
    data, _ = parse_frontmatter((PROVIDERS / f"{name}.md").read_text(encoding="utf-8"))
    return data


def load_defaults() -> dict:
    data, _ = parse_frontmatter((CONFIG / "defaults.md").read_text(encoding="utf-8"))
    return {
        "sample_size": 10,
        "batch_size": 100,
        "max_per_run": 500,
        "scope": "inbox",
        "exclude_important": True,
        **data,
    }


# --- env ------------------------------------------------------------------

def load_dotenv(path: Path | None = None) -> None:
    """Minimal KEY=VALUE .env loader. Never overrides variables already set."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
