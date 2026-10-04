# Tests

## Offline (no accounts needed)
```
uv run pytest
```
Covers the rule validator, every rule→query translation, the guard hook's allow/deny decisions, "no delete code exists" in the IMAP layer, and that `cases.yaml` agrees with the rules.

## Live tests (test accounts only)
`cases.yaml` defines 14 messages: 5 that each rule should catch, 8 negative controls (starred, too recent, unread, replied, keep-sender, important, "hotpot" vs OTP) and 1 overlap (a big old promo: `dump1` wins). Each has an expected destination.

Order for every account: **seed → dry run all rules → real run → verify → undo → verify again.**

### Gmail (step 4)
1. Do `accounts-setup.md` §1.
2. `uv run --group seed tests/seed.py seed gmail-test`: inserts the 14 messages with backdated dates (a browser window asks you to sign in once as the test account).
3. In Claude Code: **"dry run all rules on gmail-test"**. Compare the counts to `cases.yaml`: dump1 = 2, dump2 = 1, dump3 = 1, dump4 = 1, archive-old = 1.
4. **"run my cleanup on gmail-test"**, answering yes to each rule.
5. `uv run --group seed tests/seed.py verify gmail-test` prints ok/FAIL per case.
6. **"undo run <run_id>"**, then check the Inbox has everything back.
   `uv run --group seed tests/seed.py cleanup gmail-test` resets the seeded messages if you want to start over.

Note: Gmail doesn't categorise inserted mail, so `seed.py` adds the `CATEGORY_PROMOTIONS` label itself. If Gmail ignores it, `t02` still matches through its "unsubscribe" text.

### Yahoo (step 5)
1. `accounts-setup.md` §3, then `uv run tests/seed.py seed yahoo-test` (IMAP `APPEND`, backdated INTERNALDATE).
2. Same dry run / run / `verify yahoo-test` / undo cycle. Yahoo has no "important" marker, so `t12` is expected to move to `Dump2_Promos` there.
3. Smoke test first: ask Claude to call `imap_list_folders` for `yahoo-test`.

### Outlook (step 5)
Outlook.com doesn't allow IMAP for apps, so the messages are posted through Graph:
1. `uv run tests/seed.py seed outlook-test` writes `tests/out/outlook-payloads.json`.
2. Ask Claude: "seed outlook-test from tests/out/outlook-payloads.json": for each payload it creates the message with `create-draft-email` (the payload's extended properties mark it received and backdate it), moves it to the Inbox, and for `size_mb` cases attaches a ~6 MB file or sends one from the test Gmail.
3. This path is the least proven. If Graph ignores the backdating, we'll fall back to sending the old-dated cases from the test Gmail and accepting today's date for the rest, and note which age-based cases can't be tested.
4. Run the same cycle; Claude checks locations with `list-mail-folder-messages` (there's no `verify` script for Outlook).

## What "pass" means
Every case ends where `cases.yaml` says, nothing is in Trash/Spam/Deleted Items, each run's log in `logs/` has one batch entry per move, and undo puts everything back in the Inbox.
