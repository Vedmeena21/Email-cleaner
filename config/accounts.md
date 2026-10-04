# Accounts

The mailboxes mail-cleaner may work on. One row per mailbox.

- **id**: short name used in commands ("run dump1 on gmail-test") and in env var names
- **provider**: must match a file in `providers/` (`gmail`, `microsoft`, `yahoo`)
- **kind**: `test` for a throwaway account, `real` for a mailbox you care about
- **enabled**: `yes` to include it. A disabled account can't be touched at all.
- **live**: `yes` lets a `real` account be read and changed. **Only the mailbox owner sets this.** Claude must never change this column.

The guard hook (`.claude/hooks/guard.py`) and the mailcleaner server both enforce these columns.

| id | provider | address | kind | enabled | live |
|----|----------|---------|------|---------|------|
| gmail-test | gmail | change-me-test@gmail.com | test | no | no |
| outlook-test | microsoft | change-me-test@outlook.com | test | no | no |
| hotmail-test | microsoft | change-me-test@hotmail.com | test | no | no |
| yahoo-test | yahoo | change-me-test@yahoo.com | test | no | no |
| gmail-main | gmail | change-me@gmail.com | real | no | no |
