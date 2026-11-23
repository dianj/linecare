"""The customer agent: one user turn through the shared loop, with the session gate and secrets redaction."""
import json
import time
from dataclasses import dataclass, field

from linecare import db
from linecare.agents.loop import run_tool_loop, trim_history
from linecare.agents.prompts import build_customer_prompt
from linecare.redaction import redact_arguments, redact_history, redact_text
from linecare.registry import execute_tool_call
from linecare.tool_schemas import CUSTOMER_TOOLS

CAP_REACHED_REPLY = ("Sorry, I could not finish that. Please tell me again what you need, or I can connect you "
                     "with a colleague.")


@dataclass
class TurnResult:
    reply: str
    tool_calls: list = field(default_factory=list)  # [{name, arguments (redacted), status, result}]
    rounds: int = 0
    llm_ms: int = 0
    tools_ms: int = 0
    cap_reached: bool = False


def _summary(result: dict) -> str:
    """A short, secret-free line for the audit log."""
    keys = ("verified", "verdict", "outage_in_area", "account_status", "accepted", "reason", "confirmation_id")
    return ", ".join(f"{k}={result[k]}" for k in keys if k in result) or result.get("status", "")


def run_customer_turn(session, text: str, model: str) -> TurnResult:
    """Run one customer message. On success the session's history is replaced by the redacted new history.

    If the model call fails, the exception propagates and the session history is left as it was.
    """
    session.begin_turn()
    was_verified = session.verified
    history = list(session.history) + [{"role": "user", "content": text}]
    calls: list = []
    tools_ms = 0

    def execute(name: str, raw_arguments: str) -> str:
        nonlocal tools_ms
        started = time.perf_counter()
        result = execute_tool_call("customer", session, name, raw_arguments)
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
    history.append({"role": "assistant", "content": reply})
    redact_history(history, session.secrets, session.identity_phase_upto)

    trimmed = trim_history(history)
    session.identity_phase_upto = max(session.identity_phase_upto - (len(history) - len(trimmed)), 0)
    session.history = trimmed

    user_text = redact_text(text, session.secrets, identity_phase=not was_verified)
    db.log_turn(session.conv_id, "user", user_text, model)
    db.log_turn(session.conv_id, "assistant", redact_text(reply, session.secrets), model, llm_ms=llm_ms,
                tools_ms=tools_ms, rounds=rounds, tool_calls=calls)
    if session.verified and not was_verified:
        db.update_conversation(session.conv_id, account_no=session.account_no, language=session.language)
    return TurnResult(reply=reply, tool_calls=calls, rounds=rounds, llm_ms=llm_ms, tools_ms=tools_ms,
                      cap_reached=cap_reached)
