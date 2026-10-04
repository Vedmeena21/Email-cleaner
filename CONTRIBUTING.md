# Contributing

You can add rules and providers by **adding new files**. You shouldn't need to edit existing ones.

## Add a rule
Easiest: open the project in Claude Code and say **"add a rule"**. The `add-rule` skill interviews you, writes `rules/<name>.md` and validates it.

By hand:
1. Copy `rules/_TEMPLATE.md` to `rules/<name>.md` (`<name>` is kebab-case and must equal `name:` inside).
2. Fill in `description`, `target` (the label/folder), and `conditions`. All conditions must match; `any_of` means at least one group matches.

   | key | example | notes |
   |---|---|---|
   | `older_than` | `30d`, `2w`, `6m`, `2y` | months = 30 days, years = 365 |
   | `larger_than` | `500KB`, `5MB`, `1GB` | |
   | `from` | `[noreply@*, "@shop.example"]` | address patterns; `@domain` includes subdomains |
   | `subject` | `[verification code, OTP]` | whole words, case-insensitive |
   | `contains` | `[unsubscribe]` | phrases anywhere in the message |
   | `category` | `promotions` | **Gmail only** (Outlook/Yahoo skip the rule or drop that `any_of` branch) |
   | `is_read`, `replied` | `true` / `false` | `replied` only directly under `conditions` |

3. Optional: `exclusions` (same keys; a match skips the message), `order` (default 100; lower runs first, and the first rule to match wins; use 900 for broad catch-alls), `providers` (`yahoo: {skip: true}`, `gmail: {query_extra: "-label:receipts"}`).
4. A rule must narrow by age, size, sender, subject, text or category, so it can't be just `is_read: true`.
5. Validate: `uv run tools/validate_rules.py rules/<name>.md`, then `uv run pytest`.
6. Dry-run it on a **test account** ("dry run rule <name> on gmail-test"). Never on a real mailbox without the owner's say-so.
7. Open a PR containing just the new file.

Global exclusions (starred/flagged, important, keep-senders) apply to every rule automatically; don't repeat them. A rule can never delete: the only actions are move and label.

## Add a provider
A provider is a file in `providers/` plus an MCP server that can search and move mail.
1. **Pick the connector.** Confirm it can create a label/folder and move/label messages, and that it can't delete (or add the delete tools to the deny list in `.claude/settings.json`). IMAP providers can use our `mailcleaner` server as-is.
2. **Copy `providers/_TEMPLATE.md` → `providers/<name>.md`.** Frontmatter: `family` (`gmail`, `graph` or `imap`; the search dialect), `connector` (server name in `.mcp.json`), and for IMAP `imap_host` / `imap_port`. Body: tools to call, search syntax per condition, fallbacks, quirks (see the existing three).
3. If the provider uses an existing family, that's all the code you need. `plan_rule` builds its queries from `servers/mailcleaner/query.py`. A new dialect needs a builder there plus tests in `tests/test_query.py`.
4. **Wire it up:** add the server to `.mcp.json` (no secrets; use env vars from `.env`), allow its read tools and deny its delete/send tools in `.claude/settings.json`, and teach `.claude/hooks/guard.py` how that server names the account (see the `gmail` and `ms365` branches).
5. Add an example row to `config/accounts.md`, an env var to `.env.example`, a README section, and a test account guide in `tests/accounts-setup.md`.
6. `uv run pytest`, then run `tests/cases.yaml` on a test account and fix any provider-specific expectations with `expect_by`.

## Ground rules
- **Never add anything that deletes, trashes, sends or forwards mail.** `tests/test_no_delete.py` and the guard hook will fail your PR.
- No secrets, tokens or real addresses in the repo. Logs are git-ignored.
- Keep rule files short and readable. Prefer simple over clever.
