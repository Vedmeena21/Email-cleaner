#!/usr/bin/env python3
"""mailcleaner MCP server.

Serves every provider:
  plan_rule / check_candidates   turn a rule into a provider search and check results
  start_run / log_actions / finish_run / list_runs   the action log in logs/
and is the connector for IMAP providers (Yahoo):
  imap_list_folders / imap_search / imap_ensure_folder / imap_move / imap_undo

Nothing here can delete mail. Launched by Claude Code from .mcp.json:
    uv run servers/mailcleaner/server.py
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "tools")]

import mcconfig  # noqa: E402
from errors import CleanerError, SearchUnsupported  # noqa: E402
from imap_client import ImapSession  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from query import build_plan, check_candidate  # noqa: E402

mcconfig.load_dotenv()
mcp = FastMCP("mailcleaner")

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-(apply|undo)(-\d+)?$")
ACTIONS = ("create_target", "label", "move", "undo")


# --- helpers ----------------------------------------------------------------

@contextlib.contextmanager
def _friendly():
    """Turn the IMAP client's errors (message + hint) into one readable error."""
    try:
        yield
    except CleanerError as exc:
        raise RuntimeError(f"{exc} {exc.hint or ''}".strip()) from exc


def _account(key: str) -> dict:
    acct = mcconfig.find_account(key)
    ok, why = mcconfig.write_allowed(acct)
    if not ok:
        raise ValueError(f"'{key}' can't be used: {why}")
    return acct


def _rule(name: str) -> dict:
    path = mcconfig.RULES / f"{name}.md"
    if name.startswith("_") or not path.exists():
        names = [p.stem for p in mcconfig.rule_files()]
        raise ValueError(f"no rule '{name}' (have: {', '.join(names)})")
    return mcconfig.load_rule(path)


def _plan(acct: dict, rule_name: str) -> dict:
    prov = mcconfig.load_provider(acct["provider"])
    plan = build_plan(
        _rule(rule_name), prov["family"], acct["provider"],
        mcconfig.load_keep_senders(), mcconfig.load_defaults(),
    )
    return {"account": acct["id"], "address": acct["address"], "connector": prov["connector"], **plan}


def _imap(acct: dict) -> ImapSession:
    prov = mcconfig.load_provider(acct["provider"])
    if prov.get("family") != "imap":
        raise ValueError(f"'{acct['id']}' is a {acct['provider']} account; use the {prov.get('connector')} MCP server for it")
    var = mcconfig.env_name(acct["id"], "APP_PASSWORD")
    password = os.environ.get(var)
    if not password:
        raise ValueError(f"set {var} in .env (an app password for {acct['address']})")
    host = os.environ.get(mcconfig.env_name(acct["id"], "IMAP_HOST")) or prov["imap_host"]
    return ImapSession(host, int(prov.get("imap_port", 993)), acct["address"], password)


def _log_path(run_id: str, must_exist: bool = True) -> Path:
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"'{run_id}' is not a run id (they look like 20261003-151300-apply)")
    path = mcconfig.LOGS / f"{run_id}.jsonl"
    if must_exist and not path.exists():
        raise ValueError(f"no log for run '{run_id}'; call start_run first")
    return path


def _append(run_id: str, record: dict) -> None:
    line = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "run_id": run_id, **record}
    with _log_path(run_id).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def _read_log(run_id: str) -> list[dict]:
    with _log_path(run_id).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --- planning (all providers) ---------------------------------------------------

@mcp.tool()
def list_accounts() -> dict:
    """Accounts from config/accounts.md with whether mail-cleaner may use them,
    plus the rule names in run order (first match wins)."""
    out = []
    for acct in mcconfig.load_accounts():
        ok, why = mcconfig.write_allowed(acct)
        try:
            prov = mcconfig.load_provider(acct["provider"])
        except FileNotFoundError:
            prov = {}
        out.append({**acct, "allowed": ok, "why": why, "connector": prov.get("connector"), "family": prov.get("family")})
    return {"accounts": out, "rules_in_order": [r["name"] for r in mcconfig.rules_in_run_order()]}


@mcp.tool()
def plan_rule(account: str, rule: str) -> dict:
    """How to search `account` for `rule`: the provider query/filter, the target
    label/folder, post_checks to run with check_candidates, batch sizes and notes.
    If skip is true, don't run this rule on this account; report the reason."""
    return _plan(_account(account), rule)


@mcp.tool()
def check_candidates(account: str, rule: str, candidates: list[dict]) -> dict:
    """Apply the rule's exact checks (sender patterns, keep-senders, whole-word
    subjects, flags, importance, read, replied) to search results.

    Each candidate needs "id" plus whatever the search returned: "from",
    "subject", and where relevant "is_read", "flagged", "important", "replied".
    Raw Microsoft Graph messages are accepted as-is. Only ids in "keep" may be moved.
    """
    acct = _account(account)
    plan = _plan(acct, rule)
    if plan["skip"]:
        return {"keep": [], "dropped": [], "skip": True, "reason": plan["reason"]}
    keep, dropped = [], []
    for c in candidates:
        if "id" not in c:
            raise ValueError("every candidate needs an 'id'")
        ok, why = check_candidate(c, plan["post_checks"], plan["family"])
        (keep.append(c["id"]) if ok else dropped.append({"id": c["id"], "reason": why}))
    return {"keep": keep, "dropped": dropped, "kept_count": len(keep), "dropped_count": len(dropped)}


# --- action log (all providers) ---------------------------------------------------

@mcp.tool()
def start_run(mode: Literal["apply", "undo"], accounts: list[str], rules: list[str], note: str = "") -> dict:
    """Open a new action log (logs/<run_id>.jsonl) before changing any mail.
    Dry runs change nothing and need no run."""
    for a in accounts:
        _account(a)
    if mode == "apply":
        for r in rules:
            _rule(r)
    mcconfig.LOGS.mkdir(exist_ok=True)
    base = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{mode}"
    run_id, n = base, 1
    while (mcconfig.LOGS / f"{run_id}.jsonl").exists():
        n += 1
        run_id = f"{base}-{n}"
    (mcconfig.LOGS / f"{run_id}.jsonl").touch()
    _append(run_id, {"type": "run_start", "mode": mode, "accounts": accounts, "rules": rules, "note": note})
    return {"run_id": run_id, "log": f"logs/{run_id}.jsonl"}


@mcp.tool()
def log_actions(
    run_id: str,
    account: str,
    rule: str,
    action: Literal["create_target", "label", "move", "undo"],
    items: list[dict],
    target: str,
    source: str = "INBOX",
    target_id: str | None = None,
) -> dict:
    """Record one batch of changes right after it succeeds. Every move, label
    and undo must be logged so it can be reversed.

    items: one dict per message, e.g. Gmail {"id"}; Microsoft {"id", "new_id",
    "message_id"} (message_id = internetMessageId). target_id: Gmail label id /
    Graph folder id. For action=create_target pass items=[]."""
    acct = _account(account)
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    if action != "create_target" and not all(isinstance(i, dict) and i.get("id") for i in items):
        raise ValueError("every item needs an 'id'")
    _append(run_id, {
        "type": "batch", "account": acct["id"], "provider": acct["provider"], "rule": rule,
        "action": action, "source": source, "target": target, "target_id": target_id,
        "count": len(items), "items": items,
    })
    return {"logged": len(items), "log": f"logs/{run_id}.jsonl"}


@mcp.tool()
def finish_run(run_id: str, summary: dict) -> dict:
    """Close the log with the summary table shown to the user."""
    _append(run_id, {"type": "run_end", "summary": summary})
    return {"ok": True, "log": f"logs/{run_id}.jsonl"}


@mcp.tool()
def list_runs(limit: int = 10) -> list[dict]:
    """Recent runs, newest first, with how many messages each one changed."""
    runs = []
    for path in sorted(mcconfig.LOGS.glob("*.jsonl"), reverse=True)[:limit]:
        lines = _read_log(path.stem)
        start = next((l for l in lines if l.get("type") == "run_start"), {})
        counts = Counter()
        for l in lines:
            if l.get("type") == "batch":
                counts[f"{l['account']}:{l['action']}"] += l.get("count", 0)
        runs.append({
            "run_id": path.stem, "mode": start.get("mode"), "accounts": start.get("accounts"),
            "rules": start.get("rules"), "changed": dict(counts),
            "finished": any(l.get("type") == "run_end" for l in lines),
        })
    return runs


# --- IMAP (Yahoo and other IMAP providers) ------------------------------------------

@mcp.tool()
def imap_list_folders(account: str) -> list[dict]:
    """Folders on an IMAP account."""
    with _friendly(), _imap(_account(account)) as s:
        return s.list_folders()


@mcp.tool()
def imap_search(account: str, rule: str, limit: int | None = None) -> dict:
    """Dry run for an IMAP account: search the Inbox for `rule`, apply the exact
    checks and return the count, a sample, and the UIDs (oldest first, up to
    `limit` or max_per_run) to pass to imap_move. Changes nothing."""
    acct = _account(account)
    plan = _plan(acct, rule)
    if plan["skip"]:
        return plan
    notes = list(plan["notes"])
    with _friendly(), _imap(acct) as s:
        visible = s.select("INBOX", readonly=True)
        total = s.folder_message_count("INBOX")
        try:
            uids = s.search_standard([plan["criteria"]])
        except SearchUnsupported as exc:
            raise RuntimeError(f"{acct['address']} rejected the search {plan['criteria']!r}: {exc}") from exc
        summaries = s.fetch_summaries(uids) if uids else []
        uidvalidity = s.uidvalidity
    kept, reasons = [], Counter()
    for m in summaries:
        ok, why = check_candidate(
            {"id": m.uid, "from": m.sender_email, "subject": m.subject, "flagged": m.flagged},
            plan["post_checks"], "imap",
        )
        if ok:
            kept.append(m)
        else:
            reasons[why if len(why) < 80 else why[:77] + "..."] += 1
    if len(uids) >= 1000:
        notes.append("the server returned 1000 results, Yahoo's cap per search; run again after moving these")
    if total and visible and total > visible:
        notes.append(
            f"the Inbox holds {total} messages but IMAP only shows the newest {visible}; "
            "older mail becomes reachable as newer mail is moved out"
        )
    cap = limit or plan["max_per_run"]
    return {
        "account": acct["id"], "rule": rule, "target": plan["target"], "criteria": plan["criteria"],
        "matched": len(uids), "kept": len(kept), "dropped": dict(reasons.most_common(8)),
        "uidvalidity": uidvalidity, "uids": [m.uid for m in kept[:cap]],
        "sample": [
            {"uid": m.uid, "date": m.date, "from": m.sender_display, "subject": m.subject, "size_kb": m.size // 1024}
            for m in kept[: plan["sample_size"]]
        ],
        "notes": notes,
    }


@mcp.tool()
def imap_ensure_folder(account: str, rule: str) -> dict:
    """Create the rule's target folder if it doesn't exist yet."""
    acct = _account(account)
    target = _rule(rule)["target"]
    with _friendly(), _imap(acct) as s:
        created = s.create_folder(target)
    return {"target": target, "created": created}


@mcp.tool()
def imap_move(account: str, rule: str, uids: list[str], uidvalidity: int, run_id: str) -> dict:
    """Move messages found by imap_search from the Inbox into the rule's target
    folder, in batches, logging each batch to the run. Messages that no longer
    match the rule's search are left alone."""
    acct = _account(account)
    plan = _plan(acct, rule)
    if plan["skip"]:
        raise ValueError(plan["reason"])
    _log_path(run_id)
    target, batch_size = plan["target"], plan["batch_size"]
    moved, failed = 0, None
    with _friendly(), _imap(acct) as s:
        s.select("INBOX", readonly=False)
        if s.uidvalidity != uidvalidity:
            raise RuntimeError("the Inbox was renumbered since the search (UIDVALIDITY changed); run imap_search again")
        still = set(s.search_standard([plan["criteria"]]))
        todo = [u for u in uids if u in still]
        for i in range(0, len(todo), batch_size):
            batch = todo[i : i + batch_size]
            try:
                mids = s.fetch_message_ids(batch)
                res = s.move(batch, target)
            except CleanerError as exc:  # earlier batches are moved and logged
                failed = f"{exc} {exc.hint or ''}".strip()
                break
            _append(run_id, {
                "type": "batch", "account": acct["id"], "provider": acct["provider"], "rule": rule,
                "action": "move", "source": "INBOX", "target": target,
                "target_uidvalidity": res["target_uidvalidity"], "count": res["moved"],
                "items": [{"id": u, "new_id": res["new_uids"].get(u), "message_id": mids.get(u)} for u in batch],
            })
            moved += res["moved"]
    out = {"moved": moved, "target": target, "skipped_no_longer_matching": len(uids) - len(todo), "run_id": run_id}
    if failed:
        out["error"] = failed + " Run imap_search again to continue; nothing was lost."
    return out


@mcp.tool()
def imap_undo(account: str, run_id: str, apply: bool = False) -> dict:
    """Undo an IMAP run: find every message the run moved and (with apply=true)
    move it back to where it came from, logging that to the same run.
    With apply=false (the default) it only reports what it would do."""
    acct = _account(account)
    lines = _read_log(run_id)
    moves = [l for l in lines if l.get("type") == "batch" and l.get("account") == acct["id"] and l.get("action") == "move"]
    undone = {i.get("message_id") for l in lines if l.get("action") == "undo" and l.get("account") == acct["id"] for i in l["items"]}
    report = []
    with _friendly(), _imap(acct) as s:
        by_target: dict[tuple[str, str], list[tuple[dict, dict]]] = {}
        for l in moves:
            for item in l["items"]:
                if item.get("message_id") and item["message_id"] in undone:
                    continue
                by_target.setdefault((l["target"], l["source"]), []).append((l, item))
        for (target, source), entries in by_target.items():
            s.select(target, readonly=not apply)
            here = {}  # uid in target folder -> message_id
            if all(l.get("target_uidvalidity") == s.uidvalidity and i.get("new_id") for l, i in entries):
                here = s.fetch_message_ids([i["new_id"] for _, i in entries])
            if not here or len(here) < len(entries):  # fall back to matching by Message-ID
                here = s.fetch_message_ids(s.search_standard(["ALL"]))
            by_mid = {mid: uid for uid, mid in here.items()}
            found = [(by_mid[i["message_id"]], i["message_id"]) for _, i in entries if i.get("message_id") in by_mid]
            missing = [i for _, i in entries if i.get("message_id") not in by_mid]
            entry = {"target": target, "back_to": source, "found": len(found), "missing": len(missing)}
            if apply and found:
                res = s.move([u for u, _ in found], source)
                _append(run_id, {
                    "type": "batch", "account": acct["id"], "provider": acct["provider"], "rule": entries[0][0]["rule"],
                    "action": "undo", "source": target, "target": source, "count": res["moved"],
                    "items": [{"id": u, "new_id": res["new_uids"].get(u), "message_id": mid} for u, mid in found],
                })
                entry["moved_back"] = res["moved"]
            report.append(entry)
    return {"account": acct["id"], "run_id": run_id, "applied": apply, "folders": report}


if __name__ == "__main__":
    mcp.run()
