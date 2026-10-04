---
name: dump4-otp
description: One-time codes and login codes older than 30 days
target: Dump4_OTP
conditions:
  older_than: 30d
  subject: [verification code, OTP, one-time password, one time password, login code]
---
Codes expire within minutes, so there's no reason to keep them in the Inbox. Subjects are matched as whole words, so "OTP" does not catch "hotpot".
