# Test account setup (you do the sign-ins)

Use **throwaway accounts only**. Never point the tests at a mailbox you care about; `tests/seed.py` and the guard hook both refuse anything that isn't `kind: test` in `config/accounts.md`.

## 1. Throwaway Gmail  (~15 min)
1. Create a new Gmail account at <https://accounts.google.com/signup> (Google may ask for a phone number).
2. In a **browser profile signed in to the test account**, open <https://console.cloud.google.com/> and create a project, e.g. `mail-cleaner-test`.
3. **APIs & Services → Library → Gmail API → Enable.**
4. **APIs & Services → OAuth consent screen** (or *Google Auth Platform*): User type **External**, app name `mail-cleaner`, support/contact email = the test address. Add scopes `…/auth/gmail.modify`, `…/auth/gmail.labels`, `…/auth/gmail.insert`, `…/auth/gmail.readonly`. Add the test address under **Test users**.
5. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app.** Copy the client ID and secret.
6. Put them in `.env` (copy `.env.example` first):
   ```
   GOOGLE_OAUTH_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_OAUTH_CLIENT_SECRET=...
   ```
7. **Token expiry:** while the app is in *Testing* its refresh tokens expire after **7 days**. For a longer-lived setup open the consent screen and **Publish app → In production** (it stays "unverified", which is fine for personal use; you'll click through a warning at sign-in).
8. In `config/accounts.md` set the `gmail-test` row to the real address and `enabled: yes` (leave `live: no`; `live` is only for real mailboxes).
9. Restart Claude Code (it reads `.mcp.json` on start). The first Gmail tool call asks you to authorise in the browser as the test account. `workspace-mcp` stores the token in `~/.google_workspace_mcp/credentials/`.

## 2. Outlook.com test account  (~5 min)
1. Create a free address at <https://signup.live.com/> (an `@outlook.com` or `@hotmail.com` address).
2. In `config/accounts.md` set `outlook-test` to that address and `enabled: yes`.
3. Sign-in is a device code: the first `ms365` tool call (or `npx @softeria/ms-365-mcp-server@0.158.0 --login`) shows a code and a URL (<https://microsoft.com/devicelogin>). Sign in as the test account. Tokens go in the macOS Keychain.
4. Keep only this account signed in to the `ms365` server while testing. Check with `--list-accounts`, remove others with `--remove-account`.

## 3. Yahoo test account  (~5 min)
1. Create a free account at <https://login.yahoo.com/account/create>.
2. Turn on two-step verification (Account Info → Account security).
3. Account security → **Generate app password** → app name `mail-cleaner` → copy the 16-character password.
4. Put it in `.env` as `MC_YAHOO_TEST_APP_PASSWORD=...` (the variable name is `MC_` + the account id in capitals with `-` as `_` + `_APP_PASSWORD`).
5. In `config/accounts.md` set `yahoo-test` to that address and `enabled: yes`.
6. If you can't create an app password, tell me: Yahoo has been changing third-party access, and the step 5 smoke test (`imap_list_folders`) is what confirms IMAP works for your account.
