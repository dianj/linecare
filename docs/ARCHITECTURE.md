# LineCare: architecture

LineCare is a support agent for **Quillnet**, a fictional broadband, TV and mobile operator. Customers chat or talk
to it; it verifies them, diagnoses the line and the router through (mock) enterprise systems, answers from the help
articles with sources, opens CRM cases, books technician visits and hands over to a person with a summary.

All companies, customers, documents and systems in this repository are fictional. The section numbers below are
referenced from the code (for example "§6.1" in `linecare/session.py`).

Status: the customer agent, the helpdesk agent, the operator console, the metrics tab, the Salesforce adapter and
the ElevenLabs voices are built and described here (§15 for what is left).

---

## 1. Problem

A telecom support line spends most of its time on a few problems: no internet, slow Wi-Fi, a frozen router, a known
outage, an unpaid bill. Each has a clear root cause in the operator's systems, but finding it means checking several
of them, and some actions (a remote restart, a technician visit) must not happen without the customer's agreement
or a real fault. LineCare automates the diagnosis while the rules that matter stay in code.

## 2. Components

```
Browser (index.html: Customer tab with chat, microphone, chips and state strip; Operator console; Helpdesk)
   │  HTTP/JSON, audio upload
   ▼
FastAPI app (server.py)
   ├─ linecare/agents/customer.py   one customer turn: prompt with the state block, tool loop, redaction
   ├─ linecare/agents/helpdesk.py   the helpdesk agent for staff and the operator assist (§7a)
   ├─ linecare/handover_queue.py    handover queue: claim, close, assist results, CRM retry
   ├─ linecare/staff.py             staff sessions, demo tokens, roles
   ├─ linecare/agents/loop.py       shared tool-calling loop (max 8 rounds)
   ├─ linecare/agents/prompts.py    the system prompts
   ├─ linecare/tool_schemas.py      the tool schemas the model sees
   ├─ linecare/registry.py          dispatcher: allow-list, identity gate, unknown arguments dropped, never raises
   ├─ linecare/session.py           the conversation's state machine and pending confirmations (§6.1)
   ├─ linecare/tools/               tool implementations
   ├─ linecare/policy/              technician eligibility, handover priority, compensation C1-C4 (§6.3, §7a)
   ├─ linecare/kb/                  chunking, embeddings, Chroma index, access-controlled search (§8)
   ├─ linecare/crm/                 CRM interface: FakeCRM (SQLite) or SalesforceCRM (REST) (§9)
   ├─ linecare/handover.py          handover summary (§11)
   ├─ linecare/voice/               speech-to-text, text-to-speech, spoken account numbers (§10)
   ├─ linecare/redaction.py         PIN and date-of-birth redaction
   └─ linecare/db.py                SQLite: conversation log, handovers, audit log, tickets, identity locks,
                                    FakeCRM tables
mocks/                              the operator's systems: customers, network, diagnostics, billing, scheduling
                                    (seeded, deterministic; REST API under /mock, tools call the service in-process)
LLM: any OpenAI-compatible server (developed with LM Studio: Gemma 4 26B, Qwen 3.8 27B)
```

One customer turn (diagram: [linecare_diagram.svg](../linecare_diagram.svg)):
1. `POST /api/customer/chat` (text) or `/api/customer/voice` (audio → Whisper → text).
2. The session (per browser cookie) adds a **state block** to the system prompt: identity state, first name, plan,
   router, account status, reply language, open case, booked visit, pending confirmations. Never the PIN, date of
   birth, address, phone or e-mail.
3. Tool loop, up to 8 rounds. Every call goes through the dispatcher: allowed for this agent → session gate →
   arguments checked (undeclared ones dropped) → tool → JSON result with `status`.
4. After every round the history is redacted; the turn, the tool calls and the timings are logged (redacted).
5. Response: `reply`, `tool_calls` (redacted arguments, status, and for `search_help` the sources), `state`.

## 3. Design principles

1. **The model decides what to try; the code decides what is allowed.** Identity, confirmations, technician
   eligibility and the knowledge-base audience are enforced in Python.
2. **Grounded or silent.** Answers about rules and terms come from help articles with a visible source; below the
   relevance threshold the agent says it does not have the information.
3. **Least privilege.** The customer agent's search cannot reach internal documents; there is no parameter that
   could widen it.
4. **Everything external has a fake.** LLM, embeddings, CRM, speech: the tests need no GPU, network or keys.
5. **Measured.** Behaviour claims are backed by tests or by real-model evals (`evals/`).

## 4. Data

One SQLite file. The mock systems own the `sys_*` tables (reseeded at every start, so demo scenarios are always
fresh); LineCare owns the conversation log (`conversations`, `turns`, `tool_calls`), `handovers`, `assists`,
`audit_log` (staff access to customer data), `internal_tickets`, `identity_locks` (failed checks per account number)
and the FakeCRM tables (`crm_contacts`, `crm_cases`, `crm_case_comments`). Everything LineCare stores is already
redacted.

## 5. Mock systems and scenarios

`mocks/seed.py` builds 200 customers in 12 areas of 3 cities with random seed 42; PINs are stored as salted
PBKDF2-SHA256 hashes. Ten demo customers (`mocks/scenarios.py`, PINs shown in the UI) carry one fault each:

| Id | Scenario | Signal in the systems | Correct handling |
|---|---|---|---|
| S1 | Area outage | active outage for the area | tell the outage and its fix time; no tests, no case, no visit |
| S2 | Line fault | line test degraded (low SNR, many errors) | case + technician visit |
| S3 | Router frozen | router up but not handing out addresses | offer a restart, restart after "yes", check |
| S4 | Wi-Fi interference | crowded 2.4 GHz band, wired fine | offer a channel change (two steps) and the Wi-Fi tips |
| S5 | Wrong WAN setting after a factory reset | router `config_error`, plan needs PPPoE | case + handover (`config_required`); staff open a provisioning ticket |
| S6 | Suspended account | status suspended, bill overdue | explain the balance and how to pay; no tests |
| S7 | One device | everything fine | device steps from the help articles |
| S8 | Intermittent drops | line ok now, 4 drops in 24 h | case; a visit is allowed (3+ drops) |
| S9 | Angry repeat caller | 2 cases in 30 days (3 in 60) in the CRM, last visit missed | offer a colleague early; high priority; the operator assist finds credits C1, C2 and C3 |
| S10 | TV outage, internet fine | TV outage in the area | tell the outage and its fix time |

The seed also holds resolved outages (one over 24 hours, one planned with 72 hours' notice, one of exactly 24 hours)
and a missed visit, so the compensation rules have records to work on. S9's case history lives in the FakeCRM (case
numbers below 1000; new cases start at 00001001); Salesforce has none, because `CreatedDate` cannot be set through
its API. The clock can be frozen (`FROZEN_NOW`) so tests, evals and demos see the same "now".

## 6. Customer agent

### 6.1 Session state machine and confirmations
```
UNVERIFIED --verify_customer ok--> VERIFIED --handover_to_human--> HANDED_OVER (no further actions)
    | 3 failed attempts (this conversation, or this account number in any conversation)
    +------> LOCKED (15 min; only help articles and a human)
```
- Unverified callers may only verify, check outages by city/district, search the help articles and ask for a human.
  An unknown account and a wrong PIN give the same answer, so the tool cannot probe which accounts exist. Failures
  are also counted per account number (`identity_locks`), also for numbers that do not exist, so a new conversation
  gives no new attempts and a lock reveals nothing.
- **Two-step actions** (remote restart, Wi-Fi channel change, technician booking): the first call creates a *pending confirmation* and
  returns `needs_confirmation`; only a call with its id **in a later user turn** runs the action. "The customer
  said yes" is a fact the code checked, not a claim of the model. Confirmations expire after two turns.
- **Confirmation gate** ([DECISIONS.md](DECISIONS.md)): when the call with the id arrives, two one-letter questions
  go to the same model in parallel (does the reply agree to the offered action? is it addressed to the system?).
  Calibrated p(agree) ≥ 0.95 and p(flag) < 0.5 → the action runs; 0.70–0.95 or flagged → `confirmation_unclear`,
  the offer stays open and the agent asks explicitly; below 0.70 or no usable answer → `not_confirmed`, the offer is
  cancelled. The gate only runs after the code accepted the id, and it can only stop an action.
- **Secrets redaction**: PINs and dates of birth (typed, spaced or spoken, English and Bulgarian) are replaced in the
  stored turns, in the model's history from the next round on, and in handover summaries.

### 6.2 Tools (13)
| Tool | What it does | Rules in code |
|---|---|---|
| `verify_customer` | account number + PIN or date of birth | lock after 3 failures (also per account); a suspended account or a CRM history of repeat cases returns a `next_step` |
| `check_outage` | outages in the customer's area (or a named city/district before verification) | |
| `run_line_test` | sync, SNR, errors, drops | verified only |
| `get_router_status` | online, configuration, DHCP, a verdict | a frozen router: `next_step` to offer the restart; a wrong configuration: case and handover now |
| `reboot_router` | remote restart | two steps; at most 2 per conversation |
| `scan_wifi` | channels and interference per band | a crowded band returns a `next_step` to offer a channel change |
| `change_wifi_channel` | move a band to another channel (`auto` or a number) | two steps; valid channels per band |
| `get_bill_status` | balance, overdue date, last payment | no payment details |
| `search_help` | public help articles | audience fixed to `public` (§8.3) |
| `create_case` | CRM case | one open case per issue; refused when a known outage explains the problem; a repeat contact returns `suggest_handover` |
| `book_technician` | free windows → offer → booking | eligibility (§6.3), needs an open case, two steps |
| `handover_to_human` | queue a colleague with a summary | always allowed; never refused |
| `record_outcome` | "it works now" / "still broken" | only after a fix from an earlier turn |

### 6.3 Business rules
- **Technician eligibility** (`policy/technician.py`): allowed for a line fault (`no_sync`/`degraded`), a router that
  stays offline, or 3+ drops in 24 hours; refused during an area outage, for a suspended account, or before any
  diagnostics ran. A refusal carries a reason the model explains.
- **Handover priority** (`policy/escalation.py`): high for repeat contacts (2+ cases on the same issue in 30 days),
  angry customers and security locks.
- **No money from the bot**: the customer agent has no compensation tool; the prompt says staff decide; evals check
  the wording. Credits are calculated only for staff (§7a).

## 7a. Helpdesk agent and operator console

**Roles** (`linecare/staff.py`): a demo token selects `operator` or `technician` (demo authentication, documented as
such). Public documents are open to both; of the internal ones, operators read the policies (`group: policy`) and
technicians the technical notes (`group: technical`). The retriever enforces it in the Chroma filter and again in
Python, as for customers.

**Tools** (`linecare/tools/helpdesk.py`, 7; technicians get 6, without credits):
| Tool | What it does | Rules in code |
|---|---|---|
| `search_kb` | public + the role's internal documents, optional scope | role filter (§8.3) |
| `get_customer_overview` | plan, fee, status, router, cases, outages (active and 60 days), visits | needs a reason; every call is audited; no PIN, birth date, phone, e-mail or address |
| `get_case` | a CRM case with comments | |
| `add_case_comment` | internal comment | only when the user asks or approves (prompt) |
| `calculate_compensation` | credit for an outage, a missed visit or a repeat fault | the model names the event; hours, notice, fee, missed visit and case counts come from the records |
| `create_internal_ticket` | ticket for network, provisioning or billing, linked to the case | adds a case comment |
| `get_handover` | the handover summary and the last turns (stored redacted) | audited |

**Compensation** (`policy/compensation.py`, rules C1-C4 of the internal document INT-01; a test checks that both state
the same numbers): unplanned outage over 24 hours → 1/30 of the monthly fee per started day, at most 50 %; planned
work announced 48+ hours ahead → nothing; technician no-show on our side → 10 EUR; 3+ cases in one category in 60
days → up to 15 EUR goodwill with a team lead's approval (`needs_approval`); suspended account → nothing. Every rule
and boundary (24 h exactly, the cap, 48 h notice, rounding) has a unit test.

**Operator console**: the queue (high priority first, waiting time), claim (returns at once), the **assist** as a
background job the page polls, a chat with the helpdesk agent in the same conversation, and close with a note that
is added to the case. For the assist, code loads the facts first and puts them into the history as tool calls: the
handover, the customer overview, the credits the policy computes for every event on record, and the internal
procedure for the handover reason. The model then writes five labelled lines (summary, likely cause, compensation,
next step, a draft reply marked "review before sending"); the UI shows the credits from the tool results in a
table, not from the model's text.

**Credit questions in the helpdesk chat**: for operators the overview also lists `compensation_checks`, one
`calculate_compensation` call per event on record (the same list the assist uses). When an operator's message names
an account and asks about money (EN/BG words), code runs the overview and every check before the model answers, the
same way as for the assist. Found by the D4 evals: asked directly, Gemma checked two of three events, or offered to
check instead of checking; a prompt rule alone did not change it (0/3), loading the facts in code did (30/30).

**CRM outages**: a case that could not be written is kept in the handover summary (`pending_crm_sync`) and written
when the app starts again (or the CRM answers), then linked to the handover.

## 7. Prompting

The system prompt (`linecare/agents/prompts.py`) gives the diagnosis order (verify → outage → router and line
together → branch by finding → confirm the result) and the rules (one question at a time, reply in the language of
the latest message, never claim an action that did not return `ok`). Where the evals showed the model hesitating,
the hint was moved next to the data it needs: tool results carry a `next_step`, and the state block says what the
conversation is waiting for.

## 8. Knowledge base

### 8.1 Documents
`kb/public` holds 14 help articles (no internet, Wi-Fi, router lights, restarting, TV and set-top box, outages,
technician visits, bills and suspension, payment methods, plans and speeds, moving house, contract terms, privacy,
and a Bulgarian no-internet guide). `kb/internal` holds 10 staff documents: policies (compensation, escalation,
angry and repeat callers, outage communication, identity and security) and technical notes (PPPoE after a factory
reset, line test interpretation, mesh pairing, field visit checklist), plus a **canary** document with a unique
string and fake discount codes, used to prove that nothing internal reaches customers. `kb/test/PUB-99.md` is a poisoned "forum tips" article with an injected
instruction; it is indexed only for evals. Every file has front matter (`doc_id, title, audience, group, lang,
version`); the folder must match the audience.

### 8.2 Ingestion
Chunks are `##` sections; sections over 600 characters are split by paragraph with a short overlap (the embedding
model reads 128 tokens). Each chunk has a content hash: a sync re-embeds only changed chunks and removes deleted
ones. The index is one Chroma collection per embedding model, synced on first use and warmed in a background thread
at start. `python -m linecare.kb.ingest --rebuild` rebuilds it by hand.

### 8.3 Retrieval and access control
`search(query, audiences=..., groups=..., internal_groups=...)` takes the audiences from the caller; the customer
tool always passes `{"public"}`, the helpdesk tool `{"public", "internal"}` with the role's internal groups. Chroma filters on the audience, and every passage is checked again in Python: if one with a wrong
audience slips through, **no** passages are returned and the event is logged. A question counts as answerable when
the top passage scores at least the threshold calibrated for the embedding model; otherwise the tool returns no
passages and the agent says it does not know. Passages reach the model wrapped as data
(`<<passage doc_id=… section=…>> … <</passage>>`), and the wrapper cannot be closed from inside a document.

### 8.4 Embeddings
ONNX models on the CPU via fastembed (no torch; the GPU stays free for the chat model). Measured with
`evals/retrieval_eval.py` on 32 answerable and 9 unanswerable questions in English and Bulgarian, against the 14
public articles ([results](../evals/results/retrieval.md)):

| Model | hit@1 | hit@4 | questions separated by the best threshold | size |
|---|---|---|---|---|
| **paraphrase-multilingual-MiniLM-L12-v2** (chosen, threshold 0.46) | 94% | 100% | 39/41 | 0.22 GB |
| paraphrase-multilingual-mpnet-base-v2 | 91% | 100% | 39/41 | 1.0 GB |
| multilingual-e5-large | 84% | 91% | 41/41 | 2.2 GB |

e5-large ranked the one Bulgarian article first for most Bulgarian questions, whatever they asked. With the five
articles added in the helpdesk phase, two answerable questions score below MiniLM's threshold ("Do you store my PIN
when I chat with you?" and a Bulgarian question about moving house, which has no Bulgarian article), while the
best-scoring unanswerable question is at 0.458: a lower threshold would answer off-topic questions. Both fail
safely (the agent says it has no information and offers a case or a colleague). Checked in D4: a Bulgarian version
of the moving article did not help; the Bulgarian question still scored 0.419 on the English article and the
Bulgarian one ranked lower, though a sentence quoted from it scores 0.79. The limit is the small model's match for
the colloquial wording, not a missing article, so it was not kept. Hybrid BM25 was not tried: it was planned for
misses on model names and error codes, and these are ordinary phrasing; the PIN question (0.362) also scores below
off-topic questions such as "Who is the CEO of Quillnet?" (0.424), so a fix would need more than a lexical bonus on
the threshold. Tests use a
hashing `FakeEmbedder` and an in-memory index.

### 8.5 Citations
The UI shows source chips from the `search_help` results (document and section; click → the passage), not from
the model's text. The prompt asks for a short reference in the reply ("according to our Wi-Fi guide").

## 9. CRM

`linecare/crm/base.py` defines the interface (`find_contact`, `upsert_contact`, `create_case`, `get_open_case`,
`list_cases`, `get_case`, `add_comment`, `link_handover`). `FakeCRM` stores Salesforce-like Contacts, Cases
(`00001001`, …) and CaseComments in SQLite; `create_case` is idempotent per account and category while a case is
open. Case diagnostics are written from the conversation's tool log, so the CRM gets what the tools returned, not
the model's paraphrase. If the CRM fails, the tool answers `crm_unavailable` and the session remembers that a sync
is pending.

`SalesforceCRM` (`linecare/crm/salesforce.py`, plain `httpx`): an External Client App with the OAuth 2.0
client-credentials flow (connected apps are restricted for new integrations since Spring '26), the token kept in
memory and fetched again once on 401 (the flow has no refresh token), the API version discovered from
`/services/data/`. Contacts are upserted by the external id `Account_No__c`; Cases carry `Category__c`,
`Diagnostics__c` and `Handover_Id__c`; the case reference is the CaseNumber. Outages and setup errors become
`crm_unavailable`. `CRM_BACKEND` selects the backend. The contract tests run against FakeCRM, against an in-process
fake of the Salesforce REST API (in CI) and against a real Developer Edition org (`SF_LIVE_TESTS=1`); setup steps in
the README.

## 10. Voice

- **Speech-to-text**: faster-whisper `large-v3-turbo` on the CPU (int8, voice activity detection), restricted to
  English and Bulgarian (short Bulgarian clips are often labelled Russian otherwise), with a domain prompt.
- **Spoken account numbers**: "Q N one zero zero zero zero three" / "кю ен едно нула …" / "QN 100 003" →
  `QN-100003`, only when exactly six digits follow (seven heard digits mean a mishearing; the model asks again).
- **Text-to-speech**: a provider interface with edge-tts (retried, per-attempt timeout), **ElevenLabs** (REST over
  `httpx`, model `eleven_flash_v2_5`, one retry on 429/5xx, the key never logged; `TTS_PROVIDER=elevenlabs`) and a
  fake. Markdown is removed; account and case numbers are read in short digit groups. In voice turns the state
  block asks for at most two short sentences. Both online providers receive the reply text only.
- **Latency** (`evals/tts_latency.py`, [results](../evals/results/tts-latency.md)): the same four replies (EN/BG,
  short/medium), three runs each, time to the complete MP3: edge-tts median 1.42 s (slowest 6.51 s, Bulgarian
  retries), ElevenLabs 0.62 s (slowest 1.35 s). The first male voice tried was not available to a free account
  (404 `voice_not_found`), found in the live check; the defaults are premade voices
  (`ELEVENLABS_VOICE_FEMALE`, `ELEVENLABS_VOICE_MALE` change them).

## 11. Handover summary

Built in code from the session and the tool log, validated with Pydantic, stored in `handovers`:
`handover_id, created_at, priority, reason, language, customer {account_no, first_name, plan, verified},
issue {category, customer_view}, checks [{tool, result}], actions [{tool, result}], open_case_ref,
pending_crm_sync, kb_sources, sentiment, suggested_next_step, transcript_turns`. The model only adds
`customer_view`, `suggested_next_step` and `sentiment`, and that text is redacted. Completeness rules
(`summary_problems`) are used by tests and evals: all keys present, no secrets, no case or visit without checks.

## 12. HTTP API and UI

| Method & path | Purpose |
|---|---|
| `POST /api/customer/chat` | one typed turn |
| `POST /api/customer/voice` | one recorded turn (multipart audio, language `auto`/`en`/`bg`) |
| `POST /api/tts` | reply as MP3 |
| `POST /api/customer/reset`, `GET /api/customer/state` | new conversation, current state |
| `GET /api/demo-customers` | the fictional demo accounts |
| `GET /api/health`, `GET /api/models`, `POST /api/model` | LLM reachability, model list and switch |
| `POST /api/staff/login`, `GET /api/staff/me`, `POST /api/staff/logout` | staff sign-in with a demo token |
| `GET /api/operator/handovers?status=` | the queue (operator) |
| `POST /api/operator/handovers/{id}/claim` | claim; starts the assist job, returns at once (operator) |
| `GET /api/operator/handovers/{id}/assist` | `pending` / `ready` / `failed` with the card (operator) |
| `POST /api/operator/handovers/{id}/close` | close with a note that goes to the case (the operator who claimed it) |
| `POST /api/helpdesk/chat`, `POST /api/helpdesk/reset` | one helpdesk turn (any staff role), new conversation |
| `GET /api/conversations/{id}` | a stored (redacted) transcript, audited |
| `GET /api/metrics?from=&to=&model=` | KPIs of the customer conversations (any staff role; dates `YYYY-MM-DD`) |
| `/mock/...` | the mock systems (OpenAPI at `/docs`) |

The page renders all conversation text with `textContent`. Under each reply: tool chips (status colour, redacted
arguments on hover) and source chips. Quick-reply buttons ("Yes, go ahead" / "No", or in Bulgarian) appear while a
confirmation is pending; they only send a normal message. Four tabs follow the address hash (`#customer`,
`#operator`, `#helpdesk`, `#metrics`); the staff views show the queue, the handover summary, the assist card with the
policy results as a table, and the helpdesk chat with source chips.

**Metrics** (`linecare/metrics.py`, read-only queries over the conversation log). The base is every customer
conversation with at least one message, filtered by start date and model. Outcomes are set by code from tool
results and only move up (`info` < `resolved` < `case_created` < `technician_booked` < `handover`): `info` when an
informational article answered (troubleshooting steps wait for the customer's answer in `record_outcome`),
`resolved` only after a fix in an earlier turn and the customer's confirmation.

| KPI | Definition |
|---|---|
| Containment | outcome `resolved` or `info` ÷ base |
| Escalation | outcome `handover` ÷ base, by handover reason |
| By scenario | outcomes per demo scenario (S1–S10, set from the demo account at verification; others "other") |
| Messages to a confirmed fix | mean user turns in `resolved` conversations |
| Latency | median and p95 per reply: LLM (all rounds), tools, speech to text, and end to end (their sum) |
| Tool errors | tool calls with `error` / `system_error` ÷ all tool calls |
| Blocked actions | tool calls refused by a rule in code, by reason (identity, confirmation, gate, policy) |
| Fixed remotely | a restart or channel change ran and no technician was booked |

The tab draws them as tiles and plain SVG bars (no chart library), lists the last 20 conversations, and opens a
stored transcript (redacted, audited) on click. Text-to-speech time is not in the latency: the voice reply is a
separate request made by the browser after the text is shown.

## 13. Quality

- **Tests** (`tests/`, no GPU or network): session, redaction, registry, every tool's rules, technician matrix,
  CRM contract, handover summary, knowledge base (filters, post-filter, wrapper, sync), voice with fake Whisper and
  edge-tts, full HTTP turns with a scripted fake LLM, and Playwright tests for XSS, chips, confirmations and the
  microphone flow.
- **Real-model evals** (`evals/run_evals.py`): 25 conversations in 7 groups (diagnosis, confirmation, identity,
  grounding, leakage, policy, handover), graded on state first (tool calls, bookings, cases, the mock systems) and
  wording second, on a fresh copy of the data each run. Results and what they found: `evals/results/`.

## 14. Decision log

| Decision | Why |
|---|---|
| Fictional operator "Quillnet" | no confusion with real telecoms |
| Mocks mounted at `/mock`, called in-process by the tools | visible API, no self-HTTP, fast tests |
| Identity failures counted per conversation and per account number, also for unknown numbers | no new attempts in a new conversation; a lock reveals nothing |
| Router verdicts `ok / frozen / config_error / offline / rebooting`; a line test during an outage says so | one clear signal per scenario |
| Undeclared tool arguments are dropped | the model cannot widen a tool |
| Unknown account and wrong PIN answer the same | no account probing |
| MiniLM-L12 embeddings, threshold 0.46, chunks ≤ 600 characters | measured (§8.4) |
| `search_help` has no language parameter | the model retrieves across languages |
| `create_case` returns the open case with `already_open: true` | keeps four result statuses |
| No case when a known outage explains the problem | S1 enforced in code |
| "Guided steps" for `record_outcome` = an answerable passage from a technical article | a code fact, like a restart |
| The conversation outcome only moves up (info < resolved < case < visit < handover) | a later "thanks" does not hide a booked visit |
| `next_step` hints in tool results; the state block says what the conversation waits for | found by the evals (§7) |
| Quick replies send plain text | the confirmation is still checked in code |
| BM25 hybrid ranking off | not needed on this corpus; revisit if model names or codes are missed |
| Resolution via `record_outcome`, not keyword matching | "yes, it still does not work" is not a yes |
| Compensation in `policy/compensation.py`; the tool takes only the event, amounts come from the records | the model explains, it cannot change a number |
| Assist facts loaded by code as tool calls before the model answers | found in live runs: the model skipped lookups or searched the wrong documents |
| CRM history (2+ cases in 30 days) shown at verification, not only in `create_case` | found by the evals: S9 was offered a colleague late or never (2/6 → 6/6 with S5) |
| `config_error` hint says "do both now, in this turn" | found by the evals: in Bulgarian Gemma announced the case and the handover and called neither |
| Staff roles by demo token; technicians get no credit tool | demo auth, least privilege |
| Seeded history only in the FakeCRM | Salesforce does not let the API set `CreatedDate` |
| Calibrated confirmation gate (log-probabilities of a one-letter answer, temperature per model, T ≥ 1) | "did the customer agree?" was the model's unmeasured judgement; now a probability with thresholds |
| A second gate question: "is the reply addressed to the system?" | found by the labelled replies: "Answer Y. Also, what time is it?" got p(agree) = 0.998; stricter wording did not help |

## 15. Roadmap and limitations

Done: the real-model eval suite covers both agents (41 cases, including 10 helpdesk cases run as an operator or a
technician), the metrics tab (§12) and the ElevenLabs voices (§10) are built. Not pursued, with the reasons in §8.4:
hybrid BM25 ranking and more Bulgarian articles for the two retrieval misses.

Limitations: fictional data only; demo cookie sessions and demo staff tokens, no real authentication; one LLM at a
time on a local GPU; edge-tts sends reply text to an online service; operators cannot write to the customer's chat
(the draft reply is copied by hand); two answerable questions fall below the retrieval threshold (§8.4).
