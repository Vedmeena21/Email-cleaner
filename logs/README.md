# logs

Every run writes `logs/<run_id>.jsonl` (git-ignored; it contains senders and subjects). One JSON object per line:

| `type` | meaning |
|---|---|
| `run_start` | mode (`apply`/`undo`), accounts, rules, note |
| `batch` | one batch of changes: `account`, `provider`, `rule`, `action` (`create_target`, `label`, `move`, `undo`), `source`, `target`, `target_id`, `count`, `items` |
| `run_end` | the summary shown to the user |

`items` carry what undo needs: Gmail `{id}` plus the label `target_id`; Microsoft `{id, new_id, message_id}` (`message_id` is the stable `internetMessageId`, because Graph ids change when mail moves); Yahoo `{id (uid), new_id, message_id}` with `target_uidvalidity`.

## Undo
Ask Claude: "undo run 20261003-151300-apply" (the **clean-mail** skill). It previews, asks, moves everything back to the Inbox (Gmail: re-adds `INBOX`, removes the label) and logs the undo to the same file. Empty labels/folders are left in place. Nothing was ever deleted, so nothing is unrecoverable.

Ideas borrowed from [noambrand/gmail-label-cleanup](https://github.com/noambrand/gmail-label-cleanup): bounded passes (`max_per_run`) and an undo log after each run.
