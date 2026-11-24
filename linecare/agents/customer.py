"""The customer agent: one user turn through the shared loop, with the session gate and secrets redaction."""
import concurrent.futures
import json
import time
from dataclasses import dataclass, field

from linecare import config, db, decisions
from linecare.agents.loop import run_tool_loop, trim_history
from linecare.agents.prompts import build_customer_prompt
from linecare.handover import ACTION_TOOLS, DIAGNOSTIC_TOOLS, describe
from linecare.language import detect_language
from linecare.redaction import redact_arguments, redact_history, redact_text
from linecare.registry import execute_tool_call
from linecare.session import HANDED_OVER
from linecare.tool_schemas import CUSTOMER_TOOLS
from mocks.scenarios import DEMO_BY_ACCOUNT

CAP_REACHED_REPLY = ("Sorry, I could not finish that. Please tell me again what you need, or I can connect you "
                     "with a colleague.")
HANDOVER_REPLY = {
    "en": "I'm connecting you with a colleague now. You are number {position} in the queue. They will see a summary "
          "of this conversation, so you won't have to repeat anything.",
    "bg": "Свързвам ви с колега. Вие сте номер {position} на опашката. Колегата ще види резюме на разговора, така че "
          "няма да се налага да повтаряте.",
}
_gate_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="wants-human")


@dataclass
class TurnResult:
    reply: str
    tool_calls: list = field(default_factory=list)  # [{name, arguments (redacted), status, result}]
    rounds: int = 0
    llm_ms: int = 0
    tools_ms: int = 0
    cap_reached: bool = False
    gate_wait_ms: int = 0      # time the turn waited for the "wants a person" gate after the agent's answer


def _remember(session, name: str, result: dict) -> None:
    """Keep successful checks and actions for the technician policy and the handover summary."""
    if result.get("status") != "ok" or name not in DIAGNOSTIC_TOOLS + ACTION_TOOLS + ("search_help",):
        return
    if name in DIAGNOSTIC_TOOLS:
        session.diagnostics[name] = result
    try:
        line = describe(name, result)
    except (KeyError, TypeError):
        line = "ok"
    session.tool_log.append({"tool": name, "result": line, "turn": session.turn})


def _summary(result: dict) -> str:
    """A short, secret-free line for the audit log."""
    keys = ("verified", "verdict", "outage_in_area", "account_status", "accepted", "reason", "confirmation_id",
            "answerable", "case_ref", "appointment_id", "handover_id", "outcome")
    return ", ".join(f"{k}={result[k]}" for k in keys if k in result) or result.get("status", "")


def run_customer_turn(session, text: str, model: str, *, channel: str = "chat", stt_ms: int = None) -> TurnResult:
    """Run one customer message. On success the session's history is replaced by the redacted new history.

    If the model call fails, the exception propagates and the session history is left as it was.
    """
    session.begin_turn()
    session.message_language = detect_language(text)
    session.model = model
    session.last_user_text = redact_text(text, session.secrets, identity_phase=not session.verified)
    if channel == "voice" and session.channel != "voice":
        db.update_conversation(session.conv_id, channel="voice")
    session.channel = channel
    was_verified = session.verified
    wants_human = _start_wants_human_gate(session, model)
    history = list(session.history) + [{"role": "user", "content": text}]
    calls: list = []
    tools_ms = 0

    def execute(name: str, raw_arguments: str) -> str:
        nonlocal tools_ms
        started = time.perf_counter()
        result = execute_tool_call("customer", session, name, raw_arguments)
        _remember(session, name, result)
        tools_ms += int((time.perf_counter() - started) * 1000)
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        arguments = redact_arguments(name, arguments) if isinstance(arguments, dict) else {}
        calls.append({"name": name, "arguments": arguments, "status": result.get("status"), "result": result,
                      "summary": _summary(result)})
        return json.dumps(result, ensure_ascii=False)

    def after_round():
        # Everything the caller said before verification counts as identity-phase text from now on.
        if session.verified and session.identity_phase_upto < len(history):
            session.identity_phase_upto = len(history)
        if session.secrets or session.verified:
            redact_history(history, session.secrets, session.identity_phase_upto)

    msg, rounds, llm_ms = run_tool_loop(
        model=model, system_prompt=lambda: build_customer_prompt(session.state_block()), tools=CUSTOMER_TOOLS,
        history=history, execute=execute, after_round=after_round)

    cap_reached = bool(msg.tool_calls)
    reply = CAP_REACHED_REPLY if cap_reached else (msg.content or "").strip()
    started = time.perf_counter()
    handover_reply = _finish_wants_human_gate(session, wants_human, execute, calls)
    gate_wait_ms = int((time.perf_counter() - started) * 1000) if wants_human else 0
    if handover_reply:
        reply = handover_reply
    history.append({"role": "assistant", "content": reply})
    redact_history(history, session.secrets, session.identity_phase_upto)

    trimmed = trim_history(history)
    session.identity_phase_upto = max(session.identity_phase_upto - (len(history) - len(trimmed)), 0)
    session.history = trimmed

    user_text = redact_text(text, session.secrets, identity_phase=not was_verified)
    db.log_turn(session.conv_id, "user", user_text, model, stt_ms=stt_ms)
    db.log_turn(session.conv_id, "assistant", redact_text(reply, session.secrets), model, llm_ms=llm_ms,
                tools_ms=tools_ms, rounds=rounds, tool_calls=calls)
    if session.verified and not was_verified:
        demo = DEMO_BY_ACCOUNT.get(session.account_no)    # metrics: resolution by scenario (demo customers only)
        db.update_conversation(session.conv_id, account_no=session.account_no, language=session.language,
                               scenario_tag=demo.scenario if demo else None)
    return TurnResult(reply=reply, tool_calls=calls, rounds=rounds, llm_ms=llm_ms, tools_ms=tools_ms,
                      cap_reached=cap_reached, gate_wait_ms=gate_wait_ms)


def _start_wants_human_gate(session, model: str):
    """Ask "does the customer ask for a person?" in a worker thread, while the agent answers. None when off or moot."""
    if not config.DECISION_GATE or session.state == HANDED_OVER:
        return None
    context = {"previous": decisions.previous_agent_message(session.history)}
    return _gate_pool.submit(decisions.decider(model).ask_sync, decisions.WANTS_HUMAN, session.last_user_text,
                             context)


def _finish_wants_human_gate(session, future, execute, calls) -> str | None:
    """If the customer asked for a person and the agent did not hand over, hand over in code. Returns the reply
    that replaces the agent's (the agent may have answered as if it could go on), or None."""
    if future is None:
        return None
    decision = future.result()
    handover = decisions.wants_human(decision) and session.state != HANDED_OVER
    session.decisions.append({**decision.record.model_dump(mode="json"), "tool": "handover_to_human",
                              "policy_action": "handover" if handover else "none"})
    if not handover:
        return None
    # A repeat caller keeps the high priority the agent would have given (reason repeat_contact).
    reason = "repeat_contact" if session.repeat_contact or session.repeat_categories else "customer_request"
    execute("handover_to_human", json.dumps({
        "reason": reason, "customer_view": "The customer asked to talk to a person.",
        "suggested_next_step": "Continue from the summary; the assistant did not hand over by itself."}))
    calls[-1]["by"] = "wants_human_gate"
    result = calls[-1]["result"]
    if result.get("status") != "ok":
        return None
    language = session.message_language if session.message_language in HANDOVER_REPLY else "en"
    return HANDOVER_REPLY[language].format(position=result["queue_position"])
