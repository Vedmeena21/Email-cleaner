# Third-party code

`imap_client.py` and `errors.py` are adapted from **darcodev/email-cleaner**
(https://github.com/darcodev/email-cleaner), commit `542e5158b4c71e88aaa1cdcdd527d09dabd266d4`
(2026-08-22), MIT licensed. Copyright (c) 2026 Daniel. The licence text is in `LICENSE-email-cleaner`.

Changes: removed `move_to_trash`, `delete_permanently`, `empty_trash`, `_expunge`, the COPY +
`\Deleted` fallback, Trash discovery and body snippets; imports made flat; added `list_folders`,
`create_folder`, `fetch_message_ids`, a MOVE-only `move()` that records COPYUID, and UIDVALIDITY
tracking. `tests/test_no_delete.py` keeps delete code from coming back.
