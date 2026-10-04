# Mail Cleaner

**Tidy old email. Move it, never delete it.**

![rules](https://img.shields.io/badge/rules-5-blue) ![providers](https://img.shields.io/badge/providers-Gmail%20%7C%20Outlook%20%7C%20Yahoo-green) ![safety](https://img.shields.io/badge/actions-move%20%26%20label%20only-brightgreen) ![tests](https://img.shields.io/badge/tests-75%20passing-brightgreen)

## Mail Cleaner in 30 seconds

Mail Cleaner clears out old email across **Gmail, Outlook/Hotmail and Yahoo** using small rule files. You talk to [Claude Code](https://claude.com/claude-code). It searches your mailbox, shows you what a rule would move, waits for your yes, then files the mail into a label or folder and writes a log you can undo from.

```text
You:     run rule dump4-otp on gmail-test
Claude:  dump4-otp -> Dump4_OTP: 1 match
           2026-08-19  Bank Security  "Your verification code is 482913"
         Move 1 message to Dump4_OTP? (yes / skip / stop)
You:     yes
Claude:  Moved 1 message. Log: logs/20261003-185355-apply.jsonl
         To reverse: undo run 20261003-185355-apply
```

## Why you can trust it

- **Move and label only.** It never deletes, trashes, sends or forwards anything.
- **Dry run first.** Every rule shows a count and a sample before anything moves.
- **Protected mail.** Starred, flagged, important and keep-list senders are always skipped.
- **Everything is logged.** One command puts a whole run back.
- **Test accounts first.** A real mailbox stays locked until you switch it on yourself.

## Start here

| I want to... | Go to |
|---|---|
| Set it up | [Setup](#setup) |
| See what the rules do | [The rules](#the-rules) |
| Write my own rule | [Add your own rule](#add-your-own-rule) |
| Try it on a throwaway account | [tests/README.md](tests/README.md) |
| Understand the connector choices | [docs/provider-research.md](docs/provider-research.md) |
| Add a provider | [CONTRIBUTING.md](CONTRIBUTING.md) |

## The rules

Each rule is one short file in `rules/`.

| Rule | Moves | To |
|---|---|---|
| `dump1-big-files` | messages over 5 MB | `Dump1` |
| `dump2-promos` | promotions or "unsubscribe" mail, older than 2 years | `Dump2_Promos` |
| `dump3-automated` | noreply@, no-reply@, notifications@, alerts@ senders, older than 6 months | `Dump3_Automated` |
| `dump4-otp` | verification codes, OTPs and login codes, older than 30 days | `Dump4_OTP` |
| `archive-old` | read mail you never replied to, older than 3 years | `Archive_Old` |

Rules run in order, and the first rule that matches a message wins.

## Providers

| Provider | Connector | Sign-in |
|---|---|---|
| Gmail | [workspace-mcp](https://github.com/taylorwilsdon/google_workspace_mcp), organize scopes only (no send) | Google OAuth Desktop client |
| Outlook.com / Hotmail | [Softeria Microsoft 365 server](https://github.com/softeria/ms-365-mcp-server), mail tools only | Device-code sign-in |
| Yahoo | Built-in `mailcleaner` IMAP server | App password |

Gmail uses labels and removes the Inbox label. Outlook and Yahoo use real folders and move the message.

## Setup

You need [Claude Code](https://claude.com/claude-code), [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
git clone https://github.com/Vedmeena21/Email-cleaner.git
cd Email-cleaner
cp .env.example .env
uv run pytest                    # offline checks
uv run tools/validate_rules.py   # every rule validates
```

1. **List your accounts** in `config/accounts.md`: set the address and `enabled: yes`.
2. **Connect each provider** with the steps below.
3. **Open Claude Code in this folder** (it reads `.mcp.json` on start) and run `/mcp` to see the connectors.

### Gmail

1. In Google Cloud, enable the **Gmail API**, set up the OAuth consent screen (External, add your address as a test user) and create an **OAuth client of type Desktop app**.
2. Put `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`.
3. The first Gmail call opens a Google sign-in. Approve it.

### Outlook.com / Hotmail

Run the sign-in and enter the code it shows at <https://microsoft.com/devicelogin>:

```bash
MS365_MCP_TENANT_ID=consumers npx -y @softeria/ms-365-mcp-server@0.158.0 --login
```

### Yahoo

Create an app password under **Account Security > External connections > Create app password**, then add it to `.env` as `MC_<ACCOUNT_ID>_APP_PASSWORD` (for example `MC_YAHOO_TEST_APP_PASSWORD` for the account id `yahoo-test`).

Full walkthroughs, including throwaway accounts for testing, are in [tests/accounts-setup.md](tests/accounts-setup.md).

## Using it

In Claude Code, in this folder:

| Say | What happens |
|---|---|
| `dry run all rules on gmail-test` | counts and samples, nothing changes |
| `run my cleanup` | dry run, your yes, move, summary |
| `run rule dump1 on gmail-test` | one rule on one account |
| `undo run <run id>` | moves everything that run changed back |
| `add a rule` | interviews you and writes a new rule file |

## Add your own rule

A rule is a short file in `rules/`:

```markdown
---
name: dump5-newsletters
description: Old newsletters from one sender
target: Dump5_Newsletters
conditions:
  older_than: 1y
  from: ["@news.example"]
---
Newsletters I no longer read.
```

Validate it with `uv run tools/validate_rules.py`, or just tell Claude "add a rule" and it writes and checks the file for you. The available conditions are `older_than`, `larger_than`, `from`, `subject`, `contains`, `category`, `is_read`, `replied` and `any_of`.

## What's in this repo

```text
rules/        one rule per file
providers/    how each provider turns a rule into a search
config/       accounts, keep-senders, defaults
logs/         an undo log for every run
servers/      the mailcleaner MCP server (rule translation, logs, Yahoo/IMAP)
tools/        rule validator
tests/        offline tests and live test setup
.claude/      skills (clean-mail, add-rule) and the safety hook
docs/         provider research
```

## Contributing

New rules and providers are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits

The IMAP layer is adapted from [darcodev/email-cleaner](https://github.com/darcodev/email-cleaner) (MIT). See `servers/mailcleaner/THIRD_PARTY.md`.
