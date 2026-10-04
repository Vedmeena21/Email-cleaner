---
name: dump3-automated
description: Automated senders (noreply, notifications, alerts) older than 6 months
target: Dump3_Automated
conditions:
  older_than: 6m
  from: [noreply@*, no-reply@*, notifications@*, alerts@*]
---
Automated messages are rarely needed after a few months. If you rely on alerts from a particular sender, add it to `config/keep-senders.md`.
