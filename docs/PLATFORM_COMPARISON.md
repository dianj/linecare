# LineCare and two agent platforms: Cognigy.AI and Google CX Agent Studio

LineCare is built in plain Python: its own tool loop, its own rules in code, its own evals. Contact centres usually
build agents like this on a platform. This page maps each part of LineCare to its counterpart in two of them, and
says what would carry over, what the platform already gives, and what would still need code.

- **Cognigy.AI** (NiCE): flows of nodes; an AI Agent node with tool nodes, knowledge stores and handover providers.
- **Google CX Agent Studio**, part of Gemini Enterprise for Customer Experience (GECX): an agent application with a
  root agent and sub-agents, tools, Python callbacks, guardrails and a built-in evaluation.

I have not built on either platform. The platform columns come from their public documentation (read 2026-11-25,
links at the end) and describe what the docs say, not a measured comparison. Where the docs did not say something,
the table says so.

## Part by part

| LineCare part | In LineCare (code) | Cognigy.AI | CX Agent Studio |
|---|---|---|---|
| Agent and its instructions | `linecare/agents/customer.py`, `agents/prompts.py`; a tool loop in `agents/loop.py` | AI Agent node in a Flow (the docs now call it a "flow agent"): job description, instructions, tools, knowledge | root agent and sub-agents with natural-language instructions; `{@TOOL: name}` and `{@AGENT: name}` references |
| Internal helpdesk agent with roles | `agents/helpdesk.py`: 7 tools for operators, 6 for technicians | a second AI Agent node or Flow | a sub-agent, or an agent used as a tool |
| 13 tools (diagnostics, billing, cases, booking) | Python functions with JSON schemas (`tool_schemas.py`, `tools/`) | Tool child nodes (Tool ID, description, JSON-schema parameters, optional Condition); the branch below runs, a Resolve Tool Action node returns the result; MCP Tool nodes | OpenAPI, Python code, MCP, Integration Connector tools and others, run synchronously or asynchronously |
| Salesforce CRM | own adapter, `linecare/crm/salesforce.py` (OAuth client credentials, SOQL escaping, contract tests 6/6 live) | HTTP request in a tool branch, or a Salesforce handover provider for the human side | a built-in Salesforce tool type |
| Help articles with citations, internal documents kept from customers | Chroma + local embeddings; audience filter in the query **and** again in Python (`linecare/kb/`); calibrated "I don't know" threshold | Knowledge AI: knowledge stores → sources → chunks; Knowledge Tool on the agent (Top K 3, at most 5); Search Extract Output node | data store tools and file search tools |
| Identity check before account data | session state machine (`session.py`), lock after 3 failures per account | Flow logic before the agent, or a Condition on the tools | instructions plus a `before_tool_callback` (Python) |
| Confirm before a restart or a booking | two steps in code (`tools/common.py` `two_step`): the action runs only with an id offered in an earlier user turn | no confirmation setting for tools in the pages read; would be built in the tool's branch (ask, wait for the reply, then act) | no confirmation setting in the pages read; a `before_tool_callback` is the place to check it in code |
| Calibrated "did they really say yes?" gate | one-letter questions read from logprobs, temperature-scaled per model and prompt (`linecare/decide/`, [DECISIONS.md](DECISIONS.md)) | no logprob or calibrated-confidence feature in the pages read | no logprob or calibrated-confidence feature in the pages read; guardrails (below) are the nearest built-in check |
| Text in a reply aimed at the system | the gate's second question; passages wrapped as data | not stated in the pages read | "Prompt guard" guardrail; "Blocklist"; "Safety" levels |
| Rules the model must follow (no money promises, technician eligibility) | in code: `policy/technician.py`, `policy/compensation.py`, the customer agent has no compensation tool | Conditions and flow logic around the tools | "Rules" guardrails (natural language, or code through an `after_model_callback`) |
| Redaction of PINs and dates of birth | `redaction.py`: stored turns, the model's history, handover summaries | not stated in the pages read | a `before_model_callback` / `after_model_callback` could do it; not a listed built-in |
| Handover to a person | `handover_to_human` tool; summary built in code and validated; priority by policy (`policy/escalation.py`); own queue and operator console | Handover to Human Agent node with a handover provider (Live Agent, Salesforce, NiCE CXone, Genesys Cloud, 8x8, RingCentral Engage); the flow pauses and resumes after | `end_session` system tool with `liveAgentHandoff`; Agent Assist adds the human participant; a guardrail outcome can also be "Handoff to an agent" |
| Voice | faster-whisper STT; edge-tts or ElevenLabs TTS (0.62 s median) | not covered in the pages read | telephony transfer and SIP for handoff; `customize_response` system tool (voice only) |
| Testing | 346 automated tests; real-model evals graded on what the system did (tool calls, cases, bookings), two models | Interaction Panel chat with a debug mode (intents and scores, flow transitions, node output, LLM latency and tokens) | Evaluate: scenario, simulation and golden test cases; metrics such as tool correctness, goal completion, hallucinations; simulated personas |
| Tracing and metrics | runs and spans in SQLite, metrics tab (`metrics.py`) | the Interaction Panel debug mode (above) | not covered in the pages read |

## What a platform would replace, and what it would not

**Replaced by the platform:** the tool loop, the chat and voice channels, the handover queue and the connection to a
contact-centre system (Cognigy's handover providers, Agent Assist on Google), the vector store, much of the console,
and a first layer of safety (Google's prompt guard, blocklist and safety guardrails).

**Still code, on either platform:**
- **Business rules as code, not as instructions.** Technician eligibility, one open case per issue, "no money from
  the bot" and the identity lock are rules the model must not be able to talk its way around. On Cognigy they become
  flow logic and tool Conditions; on Google, Python callbacks (`before_tool_callback`) or code rules.
- **The two-step confirmation.** Neither documentation shows a "confirm before this tool runs" switch. The check that
  the offer was made in an earlier turn would be state in the flow (Cognigy) or in a callback (Google).
- **The calibrated gate.** Neither platform documents token log-probabilities or calibrated confidence for a
  decision. The gate would be its own service, called from a tool or a callback. On Google the callback sandbox has
  public internet access only, so that service would need a public endpoint.
- **Evals on outcomes.** Google's Evaluate checks tool calls and goals per test case, which covers part of what
  `evals/run_evals.py` does. Checks on the state of the systems after a conversation (was a case created, was a
  visit booked, did an internal document leak) would still be custom.

## How the concepts map, in one line each

- A LineCare tool with rules in Python ≈ a Cognigy tool node whose branch holds the rules ≈ a Google tool plus a
  `before_tool_callback`.
- `search_help` with the audience filter ≈ a Knowledge Tool / data store tool, with access control set by which
  store each agent may use.
- `handover_to_human` with its summary ≈ the Handover node / `end_session` with `liveAgentHandoff`, with the summary
  passed as context to the human agent.
- The evals ≈ Google's golden and simulation test cases; Cognigy's Interaction Panel for interactive debugging.

## Sources (read 2026-11-25)

Cognigy.AI:
- [AI Agent node](https://docs.cognigy.com/ai/agents/develop/node-reference/ai/ai-agent)
- [Handover to Human Agent node](https://docs.cognigy.com/ai/agents/develop/node-reference/service/handover-to-agent)
  and [Handovers](https://docs.cognigy.com/ai/escalate/handovers)
- [Knowledge AI](https://docs.cognigy.com/ai/empower/knowledge-ai/overview) and
  [Search Extract Output](https://docs.cognigy.com/ai/agents/develop/knowledge-ai/knowledge-search-and-extraction/search-extract-output)
- [Interaction Panel: Chat](https://docs.cognigy.com/ai/agents/test/interaction-panel/chat)

Google CX Agent Studio:
- [Overview](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio),
  [agents](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/agent),
  [instructions](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/instruction),
  [tools](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/tool),
  [system tools](https://docs.cloud.google.com/customer-engagement-ai/conversational-agents/ps/tool/system)
- [Callbacks](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/callback),
  [guardrails](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/guardrail),
  [evaluation](https://docs.cloud.google.com/gemini-enterprise-cx/cx-agent-studio/evaluation)
- [Handoff to Agent Assist](https://docs.cloud.google.com/gemini-enterprise-cx/agent-assist/handoff-cxas)
