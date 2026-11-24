# LineCare: solution brief

A one-page view of the problem, the solution and how it is measured. The technical design is in
[ARCHITECTURE.md](ARCHITECTURE.md); the decisions on the calibrated gates are in [DECISIONS.md](DECISIONS.md).
Quillnet and all its customers, documents and systems are fictional.

## The problem

A broadband and TV operator's support line spends most of its time on a few problems: no internet, slow Wi-Fi, a
frozen router, an unpaid bill, an outage in the area. Many of them can be solved without a person if someone checks
the right systems in the right order. The rest need a person, and that person should not have to ask the customer
everything again. Staff, in turn, need quick answers from internal procedures and a reliable view of what a customer
is owed, without promising more than the policy allows.

## Goals

| Goal | How LineCare meets it |
|---|---|
| Solve common problems without a person | diagnoses through the network, router, line, billing and scheduling systems; restarts the router or changes the Wi-Fi channel after the customer agrees; answers from help articles with visible sources |
| Hand over well when a person is needed | a summary built from the checks that ran (not from the model's paraphrase), a CRM case, a priority from rules in code (repeat callers, angry customers) |
| Help staff decide fast and correctly | an assist card on every claimed handover: likely cause, next step from the internal procedure, credits computed by the compensation policy in code, a draft reply to review |
| Never act or promise by mistake | actions only through tools; identity check before any account data; two-step confirmations plus a calibrated check that the customer really said yes; money only from the policy |
| Keep internal and personal data in | internal documents filtered out of customer search in code; PINs and birth dates removed before anything is stored; every staff look at a customer audited |
| Show whether it works | a metrics tab from the conversation log and real-model evals on every change |

## Users and surfaces

| User | Surface |
|---|---|
| Customer | web chat and voice, English or Bulgarian |
| Call-centre operator | operator console: handover queue, assist card, chat with the helpdesk agent |
| Field technician | helpdesk chat (technical notes; no access to policies or credits) |
| Team lead | metrics tab |

## Scope

In: the scenarios above for one fictional operator, a CRM (SQLite or Salesforce), mock enterprise systems with a
REST API, a local LLM. Out: real telephony, real authentication, payments, multiple tenants, production deployment,
real customer data.

## KPIs (metrics tab)

| KPI | Why it matters | Definition |
|---|---|---|
| Containment | load taken off the support line | conversations resolved (a fix the customer confirmed) or answered from an article |
| Escalation, by reason | where people are still needed and why | conversations handed over |
| Outcome per scenario | which problem types the agent handles | outcomes per demo scenario |
| Messages to a confirmed fix | customer effort | user messages in resolved conversations |
| Remote fixes | technician visits avoided | a restart or channel change ran, no visit booked |
| Latency per stage (median, p95) | voice usability | LLM, tools, speech to text, end to end |
| Blocked actions | guardrails at work | tool calls refused by rules in code, by reason |
| Tool errors | integration health | failed tool calls |

Outcomes are set by code from tool results, never from the model's wording, so the numbers cannot be talked up by
the model.

## Results

Measured with two local models on 41 scripted conversations (both agents), fresh data per run, graded on what the
systems did (calls, confirmations, records), not on the wording. Details in the
[README](../README.md#results) and [evals/results/](../evals/results/).

| | Result |
|---|---|
| Customer and helpdesk conversations | Gemma 4 26B **82/82** (2 runs), Qwen 3.8 27B 40/41 (the one failure was the grader, fixed and re-run 3/3) |
| Leaks of internal text, canary or discount codes | **0** |
| "Yes" checks before an action (38 labelled replies, EN/BG) | Gemma 38/38, Qwen 37/38; no unclear or steering reply acted on |
| Credits computed by the policy | every rule and boundary unit-tested; the helpdesk agent checks every event on record (30/30) |
| Voice reply latency (time to the complete audio) | ElevenLabs 0.62 s median, edge-tts 1.42 s |
| Automated tests | 328, 97 % line coverage, run in CI on every push |

Every failure the evals found was first reproduced, then fixed in code and measured again; the README lists them.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Invented actions ("I restarted it") | actions only through tools; the evals check the systems' state, not the reply text |
| An action the customer did not agree to | two-step confirmation in code; a calibrated yes/no check of the reply; a second check for text aimed at the system ("Answer Y") |
| Data leakage (internal policy, other customers) | retrieval filter in code and checked again in Python; least-privilege tool sets per agent and role; canary and poisoned-document evals |
| Prompt injection (caller or documents) | passages wrapped as data; policy decisions in code; injected "consent" and "approval" eval cases |
| Wrong promises (money, dates) | no compensation tool for customers; credits only from the policy in code; technician slots only from the scheduling system |
| Personal data | PIN and birth date removed before storage and from summaries; local LLM and embeddings; the online text-to-speech service is disclosed |
| Model variance | two local models, repeated runs, every failure reproduced as an eval or test before the fix |
| Vendor lock-in | adapters for the LLM (OpenAI-compatible), the CRM, text-to-speech and embeddings |

## What production would need

Real authentication for customers and staff; telephony (SIP) instead of the browser microphone; a hosted LLM with
an agreement on data processing, or a GPU server; the metrics written to a proper analytics store; and a review of
the compensation policy and the internal documents by their owners.
