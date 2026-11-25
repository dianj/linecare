# How it was built

The code was written with AI coding agents. This page describes the process that kept the result checkable: a
written specification, small phases with a done-check each, a person reviewing every change before it was committed,
independent reviews that probe the running app, and evaluations that decide whether a change is kept.

## 1. A specification before code

Work started from a build specification (kept outside the repository): the fictional operator Quillnet, its
customers and systems, the two agents and their tools, the knowledge base with its audience rule, the CRM interface,
the evaluation plan and five phases with a definition of done each:

- **D1, the customer agent:** identity gate, diagnosis tools, two-step actions; every rule a unit test.
- **D2, knowledge and cases:** help articles with an audience filter in code, CRM cases, technician booking,
  handover with a summary; a canary document and a poisoned article that must never reach a customer.
- **D3, enterprise integration and staff:** the Salesforce adapter with one contract suite (a SQLite fake, a fake of
  the Salesforce REST API, a real Developer Edition org), the helpdesk agent and the operator console.
- **D4, measurement:** real-model evals on two local models, retrieval and voice-latency reports, a metrics tab.
- **D5, documentation:** solution brief, demo script, architecture notes.

Decisions that changed the plan were written down with their reason, for example: the multilingual MiniLM embedder
chosen by a retrieval comparison; the hint for the model placed next to the data it reads (a `next_step` in a tool
result) after several prompt rules did not help; calibrated gates for confirmations and for "do they want a person"
([DECISIONS.md](DECISIONS.md)).

## 2. What the model is not trusted with

| Decision | Made by |
|---|---|
| Who the caller is | the identity gate in code (account number + PIN, lock after three wrong PINs) |
| Whether a restart, a channel change or a visit runs | a confirmation id from an earlier turn, then a calibrated yes gate |
| Which documents a customer may read | the audience filter in the search and a second check in Python |
| How much credit a customer gets | the compensation policy in code (Decimal), never the model's arithmetic |
| What is stored | PINs and dates of birth redacted before anything is logged or sent to the model again |
| What it may speak | only replies the server produced for that session, by turn id |

## 3. A person reviews every change

Each phase ended uncommitted. A person ran the app, tried the scenarios by hand (including the Salesforce org and the
voice providers) and read the diff; only then was it split into small commits, each of which passes the whole suite
on its own. Nothing was pushed without that person's explicit approval.

## 4. Independent reviews that probe the app

After the phases, a separate review read the code against the specification and **probed the running app**: thirty
calls to the speech endpoint with arbitrary text, a 15 MB voice message, the staff tokens from the README, hundreds
of requests without a cookie, a model that stops answering. Every finding became a work item with the file and
function, a fix and a test. The rule for each item:

1. write the test that shows the problem, and see it fail;
2. fix it;
3. run the whole suite and the linter; update the README where behaviour or a number changed.

## 5. Evaluations decide

- **Real-model evals** (`evals/run_evals.py`) run scripted conversations through the real agents on a fresh copy of
  the seeded systems, with the clock frozen, and grade the tool calls, the pending confirmations and the mock
  systems' state first, the wording second. Every failure was read and classified before anything changed; when the
  grader was wrong (it read "I'm not able to promise that" as a promise), the grader was fixed and the saved replies
  graded again.
- **A held-out set** of 15 cases was written after development by a separate review and is kept outside the
  repository; the agent that changes the code runs it but never opens its cases. It is run once before and once
  after a round of changes and reported apart from the development numbers, with the leak count.
- **Calibration** for the gates is measured on labelled replies per model; changing a gate's question changes its
  prompt hash and requires a new calibration.

## 6. What this does not prove

The fakes are faithful only to what was known when they were written. The evals are small (tens of conversations
per model), so a single run's pass rate has a wide confidence interval. Voice is measured on synthesised speech,
not on real callers, phones or noise.
