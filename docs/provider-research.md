# Provider research

Researched and checked on 2026-10-03. Each connector below was installed and its tool list read from the running server, not taken from its README.

## Summary

| Provider | Connector | Writes? | How we lock it down |
|---|---|---|---|
| Gmail | [workspace-mcp](https://github.com/taylorwilsdon/google_workspace_mcp) v2.0.0 (Python, `uvx`) | Yes: create labels, add/remove labels in bulk | `--permissions gmail:organize` requests only the `gmail.labels` and `gmail.modify` scopes, so it can't send and can't permanently delete. `--disabled-tools` hides the filter and attachment tools. |
| Outlook.com / Hotmail | [@softeria/ms-365-mcp-server](https://github.com/softeria/ms-365-mcp-server) v0.158.0 (Node, `npx`) | Yes: create folder, move message | `--enabled-tools` allows exactly 7 mail tools, and the token then asks only for `Mail.ReadWrite`, with no `Mail.Send`. |
| Yahoo | `servers/mailcleaner` (ours, Python, `uv`), built on [darcodev/email-cleaner](https://github.com/darcodev/email-cleaner)'s IMAP client | Yes: create folder, bulk `UID MOVE` | The code has no delete, expunge or `\Deleted` path. |

**Nothing we picked is read-only.** None of them can permanently delete. Two of them could still move mail to Trash or Spam, so `.claude/hooks/guard.py` blocks that.

## Gmail

**Chosen:** workspace-mcp, started as

```
uvx workspace-mcp --permissions gmail:organize --tool-tier complete \
  --disabled-tools manage_gmail_filter list_gmail_filters get_gmail_attachment_content
```

Tools exposed (checked with `tools/list`): `search_gmail_messages`, `get_gmail_message_content`, `get_gmail_messages_content_batch`, `get_gmail_thread_content`, `get_gmail_threads_content_batch`, `list_gmail_labels`, `manage_gmail_label`, `modify_gmail_message_labels`, `batch_modify_gmail_message_labels`, `start_google_auth`.

- **Search:** `search_gmail_messages(query, user_google_email, page_size, page_token, include_headers)` takes full Gmail search syntax, so `larger:5M`, `older_than:2y`, `category:promotions` and `-is:starred` all work as written.
- **Create label:** `manage_gmail_label(action="create", name=...)`. The same tool can also `update` and `delete`, so the guard hook allows only `create`.
- **Apply:** `batch_modify_gmail_message_labels(message_ids, add_label_ids, remove_label_ids, verify=True)` takes label **IDs**, not names. `verify` reads the messages back, because Gmail's batch endpoint silently ignores unknown IDs.
- **Risk:** the tool's own docstring says "to delete an email, add the TRASH label". The guard blocks `TRASH` and `SPAM` in `add_label_ids`.
- **Accounts:** every call takes `user_google_email`, so one server covers the test and real Gmail. Credentials are stored in `~/.google_workspace_mcp/credentials/`, outside the repo.
- **Setup cost:** a Google Cloud project with the Gmail API enabled, an OAuth consent screen and an OAuth client of type "Desktop app". While the app is in *Testing*, refresh tokens expire after 7 days; switching it to *In production* (unverified, personal use) avoids that.

**Rejected:**
- *Claude's built-in Gmail connector.* It exposes `label_message`/`label_thread`, but the OAuth app never requests `gmail.modify`, so label calls fail ([anthropics/claude-code#47383](https://github.com/anthropics/claude-code/issues/47383), still open). It's read-only for our purposes.
- *GongRzhe/Gmail-MCP-Server.* Archived 2026-03-03, and it ships `batch_delete_emails`.

## Microsoft (Outlook.com, Hotmail, Live)

**Chosen:** softeria, started as

```
npx -y @softeria/ms-365-mcp-server --enabled-tools \
  '^(list-mail-folders|list-mail-child-folders|list-mail-folder-messages|get-mail-message|create-mail-folder|move-mail-message|create-draft-email)$'
```

with `MS365_MCP_TENANT_ID=consumers`. With that filter, `--list-permissions` reports only `Mail.ReadWrite`.

- **Search:** `list-mail-folder-messages` on `inbox` with `$search="…"` (KQL: `from:`, `subject:`, `received<`, `size>`), `$filter`, `$select`, `$top`. Graph won't combine `$search` and `$filter` on messages, so we search with KQL and filter `isRead`, `flag` and `importance` from `$select`.
- **Create folder:** `create-mail-folder` creates a top-level folder, next to the Inbox.
- **Move:** `move-mail-message` takes one message ID and a `destinationId`. Its `destinationId` also accepts `deleteditems` and `junkemail`, so the guard blocks those. **One message per call**, which is why runs are capped.
- **IDs change on move.** We log `internetMessageId` (stable) as well as the new ID that the move returns.
- **Auth:** device-code sign-in through Softeria's built-in app registration (no Azure setup). Tokens go in the macOS Keychain. Multi-account mode adds an `account` parameter to every tool.
- **IMAP isn't an option:** Microsoft turned off password/app-password IMAP for Outlook.com on 2024-09-16, so only OAuth works.
- `create-draft-email` is enabled only so test messages can be seeded (step 5).

## Yahoo

**Chosen:** our own `servers/mailcleaner` MCP server, signing in with an app password. Its IMAP layer is vendored from darcodev/email-cleaner (MIT, commit `542e515`, 2026-08-22), with every delete path removed.

Yahoo quirks the vendored client already handles (from darcodev's notes and code):
- **Only the newest 10,000 messages of a folder are visible over IMAP.** Older mail can't be reached until newer mail moves out. A large, old Inbox takes several runs.
- **A `SEARCH` returns at most 1,000 results.** Each run re-searches until nothing matches.
- **Yahoo drops long sessions** after a few hundred moves (`BYE`). The client reconnects; because every pass re-searches, nothing is half-done.
- **`HEADER` searches are rejected** (`[CANNOT]`), and `List-Unsubscribe` is dropped from partial header fetches. We search the body for "unsubscribe" instead.
- About 5 concurrent IMAP connections per IP. We use one per account.
- Free accounts still get IMAP with an app password (Account Security → Generate app password). Some reports mention new restrictions, so the step 5 smoke test confirms it.

**Rejected IMAP MCP servers:** imap-mini-mcp, inbox-mcp, jtokib/yahoo-mail-mcp-server and boutquin/mcp-server-email. None exposes `LARGER`/`UNANSWERED` search, and most ship delete or expunge tools with no switch to turn them off.

## Existing projects reviewed

| Project | Verdict |
|---|---|
| [darcodev/email-cleaner](https://github.com/darcodev/email-cleaner) | MIT, active. **IMAP client vendored.** |
| [noambrand/gmail-label-cleanup](https://github.com/noambrand/gmail-label-cleanup) | MIT. We borrowed its *ideas* of bounded, dated passes and an undo log. |
| [marlinjai/email-mcp](https://github.com/marlinjai/email-mcp) | MIT. Fallback for bulk Outlook moves only: single maintainer, shared OAuth app by default, ships delete tools. |
| [mrpickles007/imap-cleanup-tool](https://github.com/mrpickles007/imap-cleanup-tool) | AGPL-3.0, so no code copied. |
| [axllent/imap-scrub](https://github.com/axllent/imap-scrub), [mbrt/gmailctl](https://github.com/mbrt/gmailctl) | Format ideas only. |
| Inbox Zero | Full SaaS app, Gmail and Outlook only. Not reusable. |
