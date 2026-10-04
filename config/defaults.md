---
sample_size: 10        # messages shown in each dry run
batch_size: 100        # messages moved/labelled per call (Gmail, IMAP)
max_per_run: 500       # stop a rule after this many per account per run; run again for more
scope: inbox           # rules only look at the Inbox
exclude_important: true  # Gmail is:important / Outlook importance=high are never touched
---
# Defaults

Settings that apply to every run unless you override them in the request (e.g. "run dump1 on gmail-test with max 50").

These exclusions always apply on top of each rule's own `exclusions`:
- starred / flagged messages
- important messages (`exclude_important`). Gmail sets "important" automatically on a lot of mail, so each Gmail dry run also reports how many extra messages this hides.
- every sender in `config/keep-senders.md`
