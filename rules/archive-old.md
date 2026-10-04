---
name: archive-old
description: Read mail older than 3 years that you never replied to
target: Archive_Old
order: 900                    # broad catch-all: runs after the dump rules
conditions:
  older_than: 3y
  is_read: true
  replied: false
---
You've read it, you didn't answer it, and it's three years old. It's moved out of the Inbox, but kept.
