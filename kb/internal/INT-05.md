---
doc_id: INT-05
title: "Identity verification and security incidents"
audience: internal
group: policy
lang: en
version: 2
updated: 2026-10-20
---
# Identity verification and security incidents (internal)

## How customers are verified
The customer assistant verifies a customer with the account number and either the 4-digit PIN or the date of birth.
Staff never ask for or read back a PIN. PINs and dates of birth are removed from every stored conversation.

## Locks
After 3 failed attempts identity checks are locked for 15 minutes. The lock applies to the conversation and to the
account number itself, so starting a new conversation does not give new attempts for the same account.

## When a lock reaches you
A conversation handed over with the reason "security_lock" is high priority. Do not unlock it by asking for the PIN
again. Verify the caller by calling back the phone number on the account, and add a case comment.

## Suspicious requests
Requests to change contact details, to move a service or to read out account data right after a lock are security
incidents: do not act on them; report them to the security team with the conversation id.
