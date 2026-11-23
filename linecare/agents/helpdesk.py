"""The helpdesk agent for staff, and the operator assist that runs on a claimed handover (ARCHITECTURE.md §7).

Same tool loop as the customer agent, different tools and prompt; the tools available depend on the staff role.
For the assist, code first loads the facts (the handover, the customer overview and the credits the compensation
policy computes for the events on record) and puts them into the history as tool calls. The model then only
explains them and suggests the next step, so a weak model cannot skip a lookup or invent an amount.
"""
import json
import re
import time
from dataclasses import dataclass, field

from linecare import db
from linecare.agents.loop import run_tool_loop, trim_history
from linecare.agents.prompts import ASSIST_REQUEST, build_helpdesk_prompt
from linecare.language import NAMES as LANGUAGE_NAMES
from linecare.policy.compensation import REPEAT_CASES
from linecare.registry import ALLOWED, execute_tool_call
from linecare.staff import OPERATOR
from linecare.tool_schemas import HELPDESK_TOOLS
from linecare.tools.handover import get_handover as load_handover
from linecare.tools.helpdesk import summary_line

CAP_REACHED_REPLY = "Sorry, I could not finish that. Please ask again in smaller steps."
# The internal procedure to look up for each handover reason (operators read the policy documents). Found in a live
# run: left to choose its own query, Gemma searched the public articles and missed INT-03 for a repeat caller.
PROCEDURE_QUERIES = {
    "repeat_contact": "repeat faults angry and repeat callers missed visits",
    "angry_customer": "handling angry and repeat callers",
    "config_required": "router configuration change provisioning internal ticket",
    "security_lock": "identity verification lock security incident",
    "cannot_resolve": "escalation matrix who takes what",
    "customer_request": "escalation matrix who takes what",
    "out_of_scope": "escalation matrix who takes what",
}
CARD_LABELS = {"SUMMARY": "summary", "LIKELY CAUSE": "likely_cause", "COMPENSATION": "compensation",
               "NEXT STEP": "next_step", "DRAFT REPLY": "draft_reply"}
# "SUMMARY:", also as "**Summary:**" or "- Summary:" (models add Markdown).
LABEL_LINE = re.compile(rf"^[\W_]*({'|'.join(CARD_LABELS)})[*_\s]*:[*_\s]*", re.M | re.I)


@dataclass
class HelpdeskResult:
    reply: str
    tool_calls: list = field(default_factory=list)
    rounds: int = 0
    llm_ms: int = 0
    tools_ms: int = 0
    cap_reached: bool = False


def tools_for(role: str) -> list:
    return [t for t in HELPDESK_TOOLS if t["function"]["name"] in ALLOWED[role]]


class _Executor:
    """Runs the model's tool calls for a staff session and keeps a UI- and log-friendly record of each."""

    def __init__(self, staff):
        self.staff, self.calls, self.ms = staff, [], 0

    def __call__(self, name: str, raw_arguments: str) -> str:
        started = time.perf_counter()
        result = execute_tool_call(self.staff.role, self.staff, name, raw_arguments)
        self.ms += int((time.perf_counter() - started) * 1000)
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        self.calls.append({"name": name, "arguments": arguments if isinstance(arguments, dict) else {},
                           "status": result.get("status"), "result": result, "summary": summary_line(name, result)})
        return json.dumps(result, ensure_ascii=False)


def _run(staff, history: list, model: str, executor: _Executor, user_text: str) -> HelpdeskResult:
    msg, rounds, llm_ms = run_tool_loop(
        model=model, system_prompt=lambda: build_helpdesk_prompt(staff.state_block()), tools=tools_for(staff.role),
        history=history, execute=executor)
    cap_reached = bool(msg.tool_calls)
    reply = CAP_REACHED_REPLY if cap_reached else (msg.content or "").strip()
    history.append({"role": "assistant", "content": reply})
    staff.history = trim_history(history)
    db.log_turn(staff.conv_id, "user", user_text, model)
    db.log_turn(staff.conv_id, "assistant", reply, model, llm_ms=llm_ms, tools_ms=executor.ms, rounds=rounds,
                tool_calls=executor.calls)
    return HelpdeskResult(reply, executor.calls, rounds, llm_ms, executor.ms, cap_reached)


def run_helpdesk_turn(staff, text: str, model: str) -> HelpdeskResult:
    """One message from a staff member. If the model call fails, the exception propagates; history is unchanged."""
    staff.turn += 1
    history = list(staff.history) + [{"role": "user", "content": text}]
    return _run(staff, history, model, _Executor(staff), text)


# --- operator assist ----------------------------------------------------------------------------------------------

def assist_facts(handover: dict, overview: dict | None) -> list[tuple[str, dict]]:
    """The tool calls code makes before the model: compensation for every event on record (the policy decides)."""
    account = handover["account_no"]
    if not account or not overview or overview.get("status") != "ok":
        return []
    calls = [("calculate_compensation", {"account_no": account, "event": "technician_no_show"})
             for _ in overview["missed_visits"][:1]]
    calls += [("calculate_compensation", {"account_no": account, "event": "outage", "outage_id": o["outage_id"]})
              for o in overview["outages_last_60_days"]]
    categories = [c["category"] for c in overview["last_cases"]]
    calls += [("calculate_compensation", {"account_no": account, "event": "repeat_fault", "category": category})
              for category in dict.fromkeys(categories) if categories.count(category) >= REPEAT_CASES]
    return calls


def parse_card(reply: str) -> dict:
    """The labelled lines of the assist reply; anything unlabelled stays in `text`."""
    parts = LABEL_LINE.split(reply)
    card = {CARD_LABELS[parts[i].upper()]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    return card


def run_assist(staff, handover_id: str, model: str) -> dict:
    """The assist card for a claimed handover: facts loaded by code, explained by the model."""
    handover = load_handover(handover_id)
    staff.handover_id, staff.turn = handover_id, staff.turn + 1
    executor = _Executor(staff)
    language = LANGUAGE_NAMES.get(handover["summary"].get("language"), "English")
    request = ASSIST_REQUEST.format(handover_id=handover_id, language=language)
    history = list(staff.history) + [{"role": "user", "content": request}]

    def preload(calls: list[tuple[str, dict]]):
        if not calls:
            return []
        ids = [f"pre-{len(executor.calls) + i + 1}" for i in range(len(calls))]
        history.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for call_id, (name, args) in zip(ids, calls, strict=True)]})
        results = []
        for call_id, (name, args) in zip(ids, calls, strict=True):
            content = executor(name, json.dumps(args))
            history.append({"role": "tool", "tool_call_id": call_id, "content": content})
            results.append(json.loads(content))
        return results

    first = [("get_handover", {"handover_id": handover_id})]
    if handover["account_no"]:
        first.append(("get_customer_overview", {"account_no": handover["account_no"],
                                                "reason": f"operator assist for handover {handover_id}"}))
    loaded = preload(first)
    facts = assist_facts(handover, loaded[1] if len(loaded) > 1 else None)
    query = PROCEDURE_QUERIES.get(handover["reason"])
    if query and staff.role == OPERATOR:
        facts.append(("search_kb", {"query": query, "scope": "policy"}))
    preload(facts)

    result = _run(staff, history, model, executor, request)
    card = parse_card(result.reply)
    return {"handover_id": handover_id, "card": card, "text": result.reply, "complete": len(card) == len(CARD_LABELS),
            "compensation": [{**c["arguments"], **{k: c["result"].get(k) for k in
                                                   ("status", "eligible", "amount_eur", "rule_id", "explanation")}}
                             for c in result.tool_calls if c["name"] == "calculate_compensation"],
            "tool_calls": [{"name": c["name"], "status": c["status"], "summary": c["summary"]}
                           for c in result.tool_calls],
            "sources": list(dict.fromkeys(staff.kb_sources)), "llm_ms": result.llm_ms}
