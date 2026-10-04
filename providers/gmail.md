---
family: gmail
connector: gmail            # MCP server name in .mcp.json (workspace-mcp)
---
# Gmail

Gmail has labels, not folders. "Moving" a message means adding the target label and removing `INBOX`; the message stays in All Mail, so nothing is lost.

## Tools (server `gmail`)
| Step | Tool |
|---|---|
| Search | `search_gmail_messages(query, user_google_email, page_size, page_token, include_headers=true)` |
| Find / create label | `list_gmail_labels(user_google_email, compact=true)`, then `manage_gmail_label(action="create", name=<target>)` if missing |
| Apply | `batch_modify_gmail_message_labels(message_ids, add_label_ids=[<label id>], remove_label_ids=["INBOX"], verify=true)` |
| Replied check | `get_gmail_threads_content_batch(thread_ids)` |

Labels are applied by **ID**, not name. Never add `TRASH` or `SPAM`, never use `manage_gmail_label` with anything but `create` (the guard hook blocks both).

## Search syntax
`plan_rule` builds the query for you. For reference:

| Condition | Gmail |
|---|---|
| `older_than: 2y` | `older_than:2y` (weeks become days) |
| `larger_than: 5MB` | `larger:5M` |
| `from: [noreply@*]` | `from:noreply@` (OR-ed with `{ }`) |
| `subject: [login code]` | `subject:"login code"` |
| `contains: [unsubscribe]` | `"unsubscribe"` |
| `category: promotions` | `category:promotions` |
| `is_read: true` | `is:read` |
| global | `in:inbox -is:starred -is:important -from:<keep-sender>` |

## Fallbacks
- **`replied: false`**: Gmail has no operator for it. Fetch each candidate's thread; if any message in it was sent from the account's own address, drop the candidate (`check_candidates` with `replied: true`).
- **`is:important`**: Gmail marks a lot of mail important automatically. `plan_rule` returns `query_without_important` too: run it as a count only, and tell the user how many extra messages the exclusion hides.
- Search is paged (`page_size` up to 100); follow `next_page_token` until you have `max_per_run` candidates.
