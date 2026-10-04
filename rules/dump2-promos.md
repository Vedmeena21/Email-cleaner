---
name: dump2-promos
description: Promotional mail older than 2 years
target: Dump2_Promos
conditions:
  older_than: 2y
  any_of:
    - category: promotions
    - contains: [unsubscribe]
---
Old newsletters and offers. Outlook and Yahoo have no promotions category, so on those accounts this rule uses only the "unsubscribe" test. Starred mail is always skipped.
