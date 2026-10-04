---
family: graph
connector: ms365            # MCP server name in .mcp.json (softeria ms-365-mcp-server)
---
# Microsoft (Outlook.com, Hotmail, Live)

Real folders. A rule moves each message from the Inbox into a top-level folder named after `target`.

## Tools (server `ms365`)
| Step | Tool |
|---|---|
| Search | `list-mail-folder-messages(mailFolder-id="inbox", $search / $filter, $select, $top)` |
| Find folder | `list-mail-folders` (look for `displayName` == target) |
| Create folder | `create-mail-folder(displayName=<target>)` |
| Move | `move-mail-message(message-id, destinationId=<folder id>)`: **one message per call** |

Never use `deleteditems`, `junkemail` or `archive` as a destination (the guard blocks Trash/Junk).

## Search syntax
`plan_rule` returns `mode: "filter"` or `mode: "search"`:

- **filter** (age, read state only): `$filter=receivedDateTime lt 2024-10-03T00:00:00Z and isRead eq true and flag/flagStatus ne 'flagged' and importance ne 'high'`
- **search** (size, sender, subject, text): `$search="received<2024-10-03 AND size>5242880 AND from:noreply@"` (KQL, the whole value in double quotes)

Graph can't combine `$search` with `$filter`, so in search mode flags, importance and read state come back via `$select` and `check_candidates` applies them.

## Fallbacks
- **No promotions category.** A rule needing `category` is skipped; an `any_of` branch using it is dropped, and the rest still runs (dump2 uses just "unsubscribe" here).
- **`replied: false`**: pass `$expand=singleValueExtendedProperties($filter=id eq 'Integer 0x1081')`; verb 102/103 means replied.
- **Moves are slow** (one call each). Respect `max_per_run`, and log each moved message with its `internetMessageId`, because Graph ids change on move.
- `$search` needs `ConsistencyLevel: eventual` on some tenants; personal accounts normally don't.
