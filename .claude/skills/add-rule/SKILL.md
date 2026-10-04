---
name: add-rule
description: Interview a contributor and create a new mail-cleaner rule file in rules/ in the correct format, then validate it. Use for "add a rule", "new cleanup rule", "I want to clean up <kind of email>".
---

# add-rule

Creates **one new file** `rules/<name>.md`. Never edit or delete an existing rule, provider, or config file as part of this. Read `rules/_TEMPLATE.md` and one or two existing rules for the format.

## Interview (short; skip what the user already told you)
Ask in one or two messages, not one question at a time:
1. **What should it catch?** (plain words: "newsletters from shop.example older than a year")
2. **Where should it go?** The target label/folder, like `Dump5_Newsletters`. Letters, digits, `_`, `-`, space only. It can't be a system folder (Inbox, Trash, Spam, Sent, Archive …).
3. **Conditions** (all must match). The only allowed keys:
   `older_than` (30d / 2w / 6m / 2y), `larger_than` (500KB / 5MB / 1GB), `from` (`noreply@*`, `@shop.example`), `subject` (whole-word phrases), `contains` (phrases in the message), `category` (promotions/social/updates/forums, **Gmail only**), `is_read`, `replied` (only directly under conditions), `any_of` (list of groups, at least one must match).
   A rule must narrow by at least one of age, size, sender, subject, text or category. It can't be only `is_read`.
4. **Exclusions** (optional; same keys; a match skips the message). Starred/flagged, important and keep-senders are always skipped, so don't add those.
5. **Provider caveats:** if it uses `category`, tell them Outlook and Yahoo have no categories (the rule gets skipped there, or loses that `any_of` branch). Offer `providers: {yahoo: {skip: true}}` or `{gmail: {query_extra: "..."}}` only if they ask.
6. **Order** (optional): default 100. Lower runs first and the first rule that matches a message wins; broad catch-alls should use 900.

## Create
- Name: kebab-case, e.g. `dump5-newsletters`; file `rules/dump5-newsletters.md`; the name inside must match. If the file exists, pick another name; don't overwrite.
- Keep it short: frontmatter plus one or two sentences saying why the rule exists.

## Validate
1. `uv run tools/validate_rules.py rules/<name>.md`. Fix errors and rerun until `OK`. Show the user the final file.
2. Show how it translates: `mailcleaner.plan_rule(account, rule)` for any test account (or tell them they can run it).
3. Offer a **dry run on a test account** with the clean-mail skill. Never run it live or on a `kind: real` account unless the user explicitly says so.
4. Remind them that sharing the rule is just committing the new file.
