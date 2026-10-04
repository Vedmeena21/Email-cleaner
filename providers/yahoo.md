---
family: imap
connector: mailcleaner      # our MCP server in servers/mailcleaner
imap_host: imap.mail.yahoo.com
imap_port: 993
---
# Yahoo (IMAP, app password)

Real folders over IMAP. Everything goes through the `mailcleaner` server, which signs in with the account's app password (`MC_<ID>_APP_PASSWORD` in `.env`) and can only search, create folders and `UID MOVE`.

## Tools (server `mailcleaner`)
| Step | Tool |
|---|---|
| Dry run | `imap_search(account, rule)` returns count, sample, `uids`, `uidvalidity` |
| Create folder | `imap_ensure_folder(account, rule)` |
| Move | `imap_move(account, rule, uids, uidvalidity, run_id)` (batched and logged) |
| Undo | `imap_undo(account, run_id, apply)` |

## Search syntax (IMAP SEARCH)
| Condition | IMAP |
|---|---|
| `older_than: 2y` | `BEFORE 03-Oct-2024` |
| `larger_than: 5MB` | `LARGER 5242880` |
| `from` | `FROM "noreply@"` (OR-ed) |
| `subject` | `SUBJECT "login code"` |
| `contains` | `BODY "unsubscribe"` |
| `is_read` / `replied` | `SEEN` / `UNANSWERED` |
| global | `UNFLAGGED` and `NOT FROM <keep-sender>` |

## Fallbacks and quirks
- **No category, no "important".** Category rules/branches are skipped as for Microsoft; `exclude_important` is a no-op (the plan says so).
- **`SUBJECT` is a substring match**, so "OTP" would match "hotpot". `imap_search` re-checks subjects as whole words.
- **No `HEADER` search** (Yahoo refuses it), so "unsubscribe" is a `BODY` search.
- **Only the newest 10,000 messages of a folder are visible**, and a search returns at most 1,000. Older mail appears as newer mail moves out: run again until nothing matches.
- Yahoo drops long sessions; the client reconnects and every pass re-searches, so a drop loses nothing.
- `MOVE` is required. Without it the server stops; it never falls back to copy + delete.
