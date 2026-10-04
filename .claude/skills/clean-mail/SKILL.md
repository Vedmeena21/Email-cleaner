---
name: clean-mail
description: Run the mail-cleaner cleanup rules on the configured email accounts (Gmail, Outlook/Hotmail, Yahoo) with a dry run first, confirmation, then move/label and a summary. Use for "run my cleanup", "run rule dump1 on gmail-test", "dry run all rules", or "undo run <id>".
---

# clean-mail

Moves old mail out of the Inbox using the rules in `rules/`. **Read `CLAUDE.md` safety rules first.** Never delete; only move/label.

## 0. Scope the request
- Call `mailcleaner.list_accounts`. Candidates are accounts with `allowed: true`.
- "my cleanup" / "all" → every allowed account and every rule in `rules_in_order`. "rule dump1 on gmail-test" → just those (match rule names by prefix; if ambiguous, ask).
- Any `kind: real` account: stop and confirm with the user that they want a live run, even if `live: yes`.
- Say what you're about to do in one line, e.g. "Dry-running 5 rules on gmail-test."
- Undo requests ("undo run X") go to the **Undo** section.

## 1. For each account, for each rule (in order)
1. `mailcleaner.plan_rule(account, rule)`. If `skip: true`, report the reason and move on. Read `providers/<provider>.md` for the connector's tools and quirks.
2. **Search** with the connector named in the plan:
   - **gmail**: `search_gmail_messages(query=plan.query, user_google_email, page_size=100, include_headers=true)`, following `next_page_token` until you have `max_per_run` or run out.
   - **ms365**: `list-mail-folder-messages(mailFolder-id="inbox", $filter or $search per plan.mode, $select=plan.select, $top=plan.top)`.
   - **mailcleaner (IMAP)**: `imap_search(account, rule)` does the search *and* the checks.
3. **Check:** pass the results to `mailcleaner.check_candidates(account, rule, candidates)` (not needed for IMAP). Only ids in `keep` may be moved. For Gmail rules with `replied`, first fetch each thread (`get_gmail_threads_content_batch`) and set `replied: true` on candidates whose thread has a message from the account's own address.
4. **Dry run, nothing changed yet.** Show: rule → target, the translated query, matched vs kept counts, why some were dropped, the 10 sample messages (date, from, subject, size), and any plan `notes`. For Gmail also run `plan.query_without_important` for a count only, and say how many extra the important-exclusion hides.
5. **Ask:** "Move N messages to <target> on <account>? (yes / skip / stop)". `yes` applies only to this rule and account. Wait for the answer. Unless the user said "dry run", never continue without it.

## 2. Apply (only after `yes`)
- First time in the session: `mailcleaner.start_run(mode="apply", accounts, rules)` → `run_id`.
- Create the target if missing:
  - **gmail**: `list_gmail_labels(compact=true)`; if no label named exactly `target`, `manage_gmail_label(action="create", name=target)`. Note the label id. Then `log_actions(action="create_target", items=[])`.
  - **ms365**: `list-mail-folders`; if no folder with that `displayName`, `create-mail-folder`. Note its id. Log `create_target`.
  - **IMAP**: `imap_ensure_folder(account, rule)`.
- Move in batches of `batch_size` (cap at `max_per_run` per rule per account):
  - **gmail**: `batch_modify_gmail_message_labels(message_ids, add_label_ids=[label id], remove_label_ids=["INBOX"], verify=true)`, then `log_actions(action="label", items=[{"id"}…], target, target_id)`. Check the result; if ids didn't change, say so.
  - **ms365**: `move-mail-message` once per message (`destinationId` = folder id). Collect `{id, new_id, message_id: internetMessageId}`, then `log_actions(action="move", …)` after each batch of ~25.
  - **IMAP**: `imap_move(account, rule, uids, uidvalidity, run_id)`. It logs by itself.
- If any call errors or is blocked by the guard, stop that rule, report exactly what happened and what was already logged, and ask before continuing.
- If a rule hit `max_per_run`, say so; the user can run it again for more.
- Yahoo shows only the newest 10,000 messages and returns 1,000 per search, so after a move re-run `imap_search`; repeat (asking once for the series) until it matches nothing.

## 3. Summary
`mailcleaner.finish_run(run_id, summary)`, then show a table: account | rule | matched | moved | target | skipped/notes. Give the log path (`logs/<run_id>.jsonl`) and: "To reverse: `undo run <run_id>`".

## Undo
1. `mailcleaner.list_runs` if the user didn't give an id. Read `logs/<run_id>.jsonl` and summarise what it changed, per account.
2. `mailcleaner.start_run(mode="undo", …)` and show what will be moved back; ask for a yes.
3. Reverse each batch:
   - **gmail**: `batch_modify_gmail_message_labels(message_ids, add_label_ids=["INBOX"], remove_label_ids=[target_id])`, then `log_actions(action="undo")`.
   - **ms365**: `move-mail-message` back to `inbox`. If a stored `new_id` fails, find the message by `message_id` (internetMessageId) in the target folder and use its current id.
   - **IMAP**: `imap_undo(account, run_id, apply=false)` to preview, then `apply=true` after the yes.
4. Never delete the now-empty label or folder; leave it.

## Hard stops
Don't run a rule that failed validation (`uv run tools/validate_rules.py`). Don't work around the guard hook, edit `config/accounts.md`, or touch a mailbox that isn't allowed.
