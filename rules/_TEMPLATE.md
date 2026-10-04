---
name: my-rule-name            # kebab-case, must equal the filename (my-rule-name.md)
description: One line saying what this catches
target: My_Target             # Gmail label / Outlook & Yahoo folder. Letters, digits, _ - space.
# order: 100                  # optional, 1-999 (default 100). Lower runs first; the first
                              # rule to match a message wins. Broad catch-alls use 900.
conditions:                   # ALL of these must match
  older_than: 1y              # Nd | Nw | Nm | Ny   (30d, 6m, 2y)
  # larger_than: 5MB          # NKB | NMB | NGB
  # from: [noreply@*, "@shop.example"]          # address patterns, any may match
  # subject: [receipt, "order confirmation"]    # phrases, any may match (whole words)
  # contains: [unsubscribe]                     # phrases anywhere in the message
  # category: promotions      # promotions | social | updates | forums (Gmail only)
  # is_read: true
  # replied: false
  # any_of:                   # at least ONE of these groups must match
  #   - category: promotions
  #   - contains: [unsubscribe]
exclusions: {}                # same keys as conditions; a match here SKIPS the message
providers: {}                 # optional per provider, e.g.
                              #   yahoo: {skip: true}
                              #   gmail: {query_extra: "-label:receipts"}
---
Why this rule exists, in a sentence or two. Starred/flagged, important and keep-senders
mail is always skipped; you don't need to repeat that here.

Copy this file to `rules/<name>.md`, or ask Claude: "add a rule".
