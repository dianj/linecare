---
doc_id: INT-06
title: "QN-Box 4: PPPoE and WAN configuration after a factory reset"
audience: internal
group: technical
lang: en
version: 3
updated: 2026-09-28
---
# QN-Box 4: PPPoE and WAN configuration after a factory reset

## Symptom
After a customer presses the reset button for more than 10 seconds, the QN-Box 4 returns to factory settings with
the WAN mode set to DHCP. DSL plans need PPPoE, so the router is online on the management network but has no
internet: the router status shows "config_error" and the WAN is not up. A remote restart does not help.

## Which plans need PPPoE
DSL 50 uses PPPoE. All fiber plans use DHCP and are not affected by a factory reset.

## Fix (provisioning)
The customer assistant cannot change router configuration. Open an internal ticket for the provisioning team with
the case number: provisioning pushes the PPPoE profile remotely within 30 minutes. Do not give PPPoE credentials to
the customer by chat or phone.

## After the fix
Ask the customer to wait two minutes, then check the router status again: the verdict should be "ok". Add a case
comment and close the handover.
