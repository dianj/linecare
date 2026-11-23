---
doc_id: INT-02
title: "Escalation matrix and priorities"
audience: internal
group: policy
lang: en
version: 2
updated: 2026-10-12
---
# Escalation matrix and priorities (internal)

## When the assistant hands over
The customer assistant hands a conversation to the operator queue when the customer asks for a person (always, at
any point, also before identity verification), when it cannot solve the problem, when a router configuration change
is needed, when identity checks are locked, or when the customer is angry or a repeat contact.

## Priority
A handover is high priority when the reason is a repeat contact, an angry customer or a security lock, or when the
customer had 2 or more cases in the same category within 30 days. Everything else is normal priority. High priority
handovers are claimed first.

## Who takes what
Operators claim handovers from the queue in the operator console. Configuration and line faults that need work on
the network go to the network or provisioning team as an internal ticket, linked to the case. Billing disputes go
to the billing team.

## Closing
An operator closes a handover only after the customer has been answered and the case has a comment saying what was
done. Credits are calculated with the compensation tool, never by hand.
