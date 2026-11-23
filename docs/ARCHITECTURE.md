# LineCare: architecture

LineCare is a support agent for **Quillnet**, a fictional broadband, TV and mobile operator. Customers chat or talk
to it; it verifies them, diagnoses the line and the router through (mock) enterprise systems, answers from the help
articles with sources, opens CRM cases, books technician visits and hands over to a person with a summary.

All companies, customers, documents and systems in this repository are fictional. The section numbers below are
referenced from the code (for example "§6.1" in `linecare/session.py`).

Status: the customer side is built (this document describes it). The internal helpdesk agent, the operator console,
metrics, ElevenLabs voices and the live Salesforce adapter are planned (§15).

---

## 1. Problem

A telecom support line spends most of its time on a few problems: no internet, slow Wi-Fi, a frozen router, a known
outage, an unpaid bill. Each has a clear root cause in the operator's systems, but finding it means checking several
of them, and some actions (a remote restart, a technician visit) must not happen without the customer's agreement
or a real fault. LineCare automates the diagnosis while the rules that matter stay in code.

## 2. Components

```
Browser (index.html: chat, microphone, tool and source chips, state strip)
   │  HTTP/JSON, audio upload
   ▼
FastAPI app (server.py)
   ├─ linecare/agents/customer.py   one customer turn: prompt with the state block, tool loop, redaction
   ├─ linecare/agents/loop.py       shared tool-calling loop (max 8 rounds)
   ├─ linecare/agents/prompts.py    the system prompt
   ├─ linecare/tool_schemas.py      the tool schemas the model sees
   ├─ linecare/registry.py          dispatcher: allow-list, identity gate, unknown arguments dropped, never raises
   ├─ linecare/session.py           the conversation's state machine and pending confirmations (§6.1)
   ├─ linecare/tools/               tool implementations
   ├─ linecare/policy/              technician eligibility, handover priority (§6.3)
   ├─ linecare/kb/                  chunking, embeddings, Chroma index, access-controlled search (§8)
   ├─ linecare/crm/                 CRM interface + FakeCRM (§9)
   ├─ linecare/handover.py          handover summary (§11)
   ├─ linecare/voice/               speech-to-text, text-to-speech, spoken account numbers (§10)
   ├─ linecare/redaction.py         PIN and date-of-birth redaction
   └─ linecare/db.py                SQLite: conversation log, handovers, FakeCRM tables
mocks/                              the operator's systems: customers, network, diagnostics, billing, scheduling
                                    (seeded, deterministic; REST API under /mock, tools call the service in-process)
LLM: any OpenAI-compatible server (developed with LM Studio: Gemma 4 26B, Qwen 3.8 27B)
```

One customer turn:
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
fresh); LineCare owns the conversation log (`conversations`, `turns`, `tool_calls`), `handovers` and the FakeCRM
tables (`crm_contacts`, `crm_cases`, `crm_case_comments`). Everything LineCare stores is already redacted.

## 5. Mock systems and scenarios

`mocks/seed.py` builds 200 customers in 12 areas of 3 cities with random seed 42; PINs are stored as salted
PBKDF2-SHA256 hashes. Ten demo customers (`mocks/scenarios.py`, PINs shown in the UI) carry one fault each:

| Id | Scenario | Signal in the systems | Correct handling |
|---|---|---|---|
| S1 | Area outage | active outage for the area | tell the outage and its fix time; no tests, no case, no visit |
| S2 | Line fault | line test degraded (low SNR, many errors) | case + technician visit |
| S3 | Router frozen | router up but not handing out addresses | offer a restart, restart after "yes", check |
| S4 | Wi-Fi interference | crowded 2.4 GHz band, wired fine | Wi-Fi tips from the help articles (5 GHz, placement) |
| S6 | Suspended account | status suspended, bill overdue | explain the balance and how to pay; no tests |
| S7 | One device | everything fine | device steps from the help articles |

S5 (wrong WAN setting), S8 (intermittent drops), S9 (repeat caller) and S10 (TV outage) are seeded for the helpdesk
phase. The clock can be frozen (`FROZEN_NOW`) so tests, evals and demos see the same "now".

## 6. Customer agent

### 6.1 Session state machine and confirmations
```
UNVERIFIED --verify_customer ok--> VERIFIED --handover_to_human--> HANDED_OVER (no further actions)
    | 3 failed attempts
    +------> LOCKED (15 min; only help articles and a human)
```
- Unverified callers may only verify, check outages by city/district, search the help articles and ask for a human.
  An unknown account and a wrong PIN give the same answer, so the tool cannot probe which accounts exist.
- **Two-step actions** (remote restart, technician booking): the first call creates a *pending confirmation* and
  returns `needs_confirmation`; only a call with its id **in a later user turn** runs the action. "The customer
  said yes" is a fact the code checked, not a claim of the model. Confirmations expire after two turns.
- **Secrets redaction**: PINs and dates of birth (typed, spaced or spoken, English and Bulgarian) are replaced in the
  stored turns, in the model's history from the next round on, and in handover summaries.

### 6.2 Tools (12)
| Tool | What it does | Rules in code |
|---|---|---|
| `verify_customer` | account number + PIN or date of birth | lock after 3 failures; a suspended account returns a `next_step` |
| `check_outage` | outages in the customer's area (or a named city/district before verification) | |
| `run_line_test` | sync, SNR, errors, drops | verified only |
| `get_router_status` | online, configuration, DHCP, a verdict | a frozen router returns a `next_step` to offer the restart |
| `reboot_router` | remote restart | two steps; at most 2 per conversation |
| `scan_wifi` | channels and interference per band | |
| `get_bill_status` | balance, overdue date, last payment | no payment details |
| `search_help` | public help articles | audience fixed to `public` (§8.3) |
| `create_case` | CRM case | one open case per issue; refused when a known outage explains the problem |
| `book_technician` | free windows → offer → booking | eligibility (§6.3), needs an open case, two steps |
| `handover_to_human` | queue a colleague with a summary | always allowed; never refused |
| `record_outcome` | "it works now" / "still broken" | only after a fix from an earlier turn |

### 6.3 Business rules
- **Technician eligibility** (`policy/technician.py`): allowed for a line fault (`no_sync`/`degraded`), a router that
  stays offline, or 3+ drops in 24 hours; refused during an area outage, for a suspended account, or before any
  diagnostics ran. A refusal carries a reason the model explains.
- **Handover priority** (`policy/escalation.py`): high for repeat contacts (2+ cases on the same issue in 30 days),
  angry customers and security locks.
- **No money from the bot**: there is no compensation tool; the prompt says staff decide; evals check the wording.

## 7. Prompting

The system prompt (`linecare/agents/prompts.py`) gives the diagnosis order (verify → outage → router and line
together → branch by finding → confirm the result) and the rules (one question at a time, reply in the language of
the latest message, never claim an action that did not return `ok`). Where the evals showed the model hesitating,
the hint was moved next to the data it needs: tool results carry a `next_step`, and the state block says what the
conversation is waiting for.

## 8. Knowledge base

### 8.1 Documents
`kb/public` holds nine help articles (no internet, Wi-Fi, router lights, restarting, outages, technician visits,
bills and suspension, payment methods, and a Bulgarian no-internet guide). `kb/internal` holds staff documents: the
compensation policy and a **canary** document with a unique string and fake discount codes, used to prove that
nothing internal reaches customers. `kb/test/PUB-99.md` is a poisoned "forum tips" article with an injected
instruction; it is indexed only for evals. Every file has front matter (`doc_id, title, audience, group, lang,
version`); the folder must match the audience.

### 8.2 Ingestion
Chunks are `##` sections; sections over 600 characters are split by paragraph with a short overlap (the embedding
model reads 128 tokens). Each chunk has a content hash: a sync re-embeds only changed chunks and removes deleted
ones. The index is one Chroma collection per embedding model, synced on first use and warmed in a background thread
at start. `python -m linecare.kb.ingest --rebuild` rebuilds it by hand.

### 8.3 Retrieval and access control
`search(query, audiences=..., groups=...)` takes the audiences from the caller; the customer tool always passes
`{"public"}`. Chroma filters on the audience, and every passage is checked again in Python: if one with a wrong
audience slips through, **no** passages are returned and the event is logged. A question counts as answerable when
the top passage scores at least the threshold calibrated for the embedding model; otherwise the tool returns no
passages and the agent says it does not know. Passages reach the model wrapped as data
(`<<passage doc_id=… section=…>> … <</passage>>`), and the wrapper cannot be closed from inside a document.

### 8.4 Embeddings
ONNX models on the CPU via fastembed (no torch; the GPU stays free for the chat model). Measured with
`evals/retrieval_eval.py` on 24 answerable and 9 unanswerable questions in English and Bulgarian
([results](../evals/results/retrieval.md)):

| Model | hit@4 | questions separated by the best threshold | size |
|---|---|---|---|
| **paraphrase-multilingual-MiniLM-L12-v2** (chosen, threshold 0.46) | 100% | 33/33 | 0.22 GB |
| paraphrase-multilingual-mpnet-base-v2 | 100% | 32/33 | 1.0 GB |
| multilingual-e5-large | 92% | 32/33 | 2.2 GB |

e5-large ranked the one Bulgarian article first for most Bulgarian questions, whatever they asked. Tests use a
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
is pending. The same contract tests will run against the Salesforce REST adapter (§15).

## 10. Voice

- **Speech-to-text**: faster-whisper `large-v3-turbo` on the CPU (int8, voice activity detection), restricted to
  English and Bulgarian (short Bulgarian clips are often labelled Russian otherwise), with a domain prompt.
- **Spoken account numbers**: "Q N one zero zero zero zero three" / "кю ен едно нула …" / "QN 100 003" →
  `QN-100003`, only when exactly six digits follow (seven heard digits mean a mishearing; the model asks again).
- **Text-to-speech**: a provider interface with edge-tts (retried, per-attempt timeout) and a fake. Markdown is
  removed; account and case numbers are read in short digit groups. In voice turns the state block asks for at
  most two short sentences.

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
| `/mock/...` | the mock systems (OpenAPI at `/docs`) |

The page renders all conversation text with `textContent`. Under each reply: tool chips (status colour, redacted
arguments on hover) and source chips. Quick-reply buttons ("Yes, go ahead" / "No", or in Bulgarian) appear while a
confirmation is pending; they only send a normal message.

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
| Identity attempts per conversation (per account comes with the operator console) | enough for the customer flow |
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

## 15. Roadmap and limitations

Planned: an internal helpdesk agent (staff roles, internal documents, compensation calculated in code), an operator
console with the handover queue and an assist panel, metrics, ElevenLabs voices, and a Salesforce REST adapter
(Developer Edition, client-credentials flow) behind the same CRM contract.

Limitations: fictional data only; demo cookie sessions, no real authentication; one LLM at a time on a local GPU;
edge-tts sends reply text to an online service; the identity lock is per conversation.
