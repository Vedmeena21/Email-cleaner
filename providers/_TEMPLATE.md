---
family: imap                # how it is searched: gmail | graph | imap
connector: mailcleaner      # MCP server name from .mcp.json that does the moving
imap_host: imap.example.com # imap family only
imap_port: 993              # imap family only
---
# <Provider name>

Copy to `providers/<name>.md` (the filename is the provider name used in `config/accounts.md` and in a rule's `providers:` block).

Cover, briefly: the tools to call for search / create target / move; the search syntax per condition; fallbacks for conditions it can't do (and whether the rule is skipped or just loses a branch); and quirks. If it needs a new search family, `servers/mailcleaner/query.py` needs a builder; see CONTRIBUTING.md.
