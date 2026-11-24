"""Tool results. Every tool returns a dict with `status`: ok | error | blocked | needs_confirmation."""
import re

ACCOUNT_NO = re.compile(r"QN-\d{6}")


def ok(**fields) -> dict:
    return {"status": "ok", **fields}


def error(message: str, **fields) -> dict:
    """The call was wrong in a way the model can fix (or ask the customer about)."""
    return {"status": "error", "message": message, **fields}


def blocked(reason: str, message: str, **fields) -> dict:
    """A rule in code refused the call. `reason` is a stable code; `message` tells the model what to do."""
    return {"status": "blocked", "reason": reason, "message": message, **fields}


def normalise_account_no(raw) -> str | None:
    """'qn 100001', 'QN100001' or '100001' -> 'QN-100001'; None if it cannot be an account number."""
    text = re.sub(r"[\s\-_.]", "", str(raw or "")).upper()
    if text.startswith("QN"):
        text = text[2:]
    return f"QN-{text}" if re.fullmatch(r"\d{6}", text) else None


def two_step(session, tool: str, arguments: dict, summary: str, confirmation_id, execute) -> dict:
    """The shared confirmation flow for side-effect tools (ARCHITECTURE.md §6.1).

    Without confirmation_id: store a pending action and ask the model to get the customer's answer.
    With it: run `execute(stored_arguments)` only if the action was offered in an earlier user turn.
    """
    if not confirmation_id:
        action = session.create_pending(tool, arguments, summary)
        return {"status": "needs_confirmation", "confirmation_id": action.confirmation_id, "summary": summary,
                "message": "Nothing has been done yet. Tell the customer what will happen and ask whether to go "
                           "ahead. Call this tool again with this confirmation_id only after the customer agrees "
                           "in their next message."}
    action, reason = session.take_pending(tool, confirmation_id)
    if action is None:
        if reason == "confirm_in_next_turn":
            return blocked(reason, "The customer has not answered yet. Ask them first and wait for their reply.")
        if reason == "confirmation_for_other_tool":
            other = session.pending[str(confirmation_id).strip().upper()].tool
            return blocked(reason, f"Nothing was done: this confirmation_id is for {other}, not {tool}. If the "
                                   f"customer agreed to that offer, call {other} with this confirmation_id.")
        return blocked(reason, "This confirmation is unknown or has expired. Call the tool without "
                               "confirmation_id to make a new offer, and ask the customer.")
    refusal = confirmation_gate(session, action)
    if refusal:
        return refusal
    return execute(action.arguments)


def confirmation_gate(session, action) -> dict | None:
    """The second key: a calibrated check that the customer's latest message agrees to this action. None = go."""
    from linecare import config, decisions
    from linecare.decide import Action

    if not config.DECISION_GATE:
        return None
    agree, instruction = decisions.decider(session.model).ask_many_sync(
        [decisions.CONFIRM_ACTION, decisions.INSTRUCTION], session.last_user_text,
        {"action": action.summary or action.tool})
    verdict = decisions.confirmation_action(agree, instruction)
    p_yes = round(agree.probs["yes"], 3) if agree.probs else None
    for decision in (agree, instruction):
        session.decisions.append({**decision.record.model_dump(mode="json"), "tool": action.tool,
                                  "policy_action": verdict.value})
    if verdict == Action.ACT:
        return None
    if verdict == Action.CONFIRM:
        session.pending[action.confirmation_id] = action          # the offer stays open for the next answer
        return blocked("confirmation_unclear",
                       f"Nothing was done: the customer's reply is not a clear yes to \"{action.summary}\". "
                       "Answer any question they asked, then ask one explicit yes/no question. Call the tool again "
                       "with the same confirmation_id only after a clear yes.",
                       confirmation_id=action.confirmation_id, p_yes=p_yes)
    return blocked("not_confirmed",
                   "Nothing was done and the offer is cancelled: the customer did not clearly agree. Do not do it. "
                   "If they ask for it again later, make a new offer.", p_yes=p_yes)


# A conversation's outcome only moves up this list: a booked visit stays the outcome even if the customer later
# says thanks, and a handover wins over everything.
OUTCOME_RANK = ["info", "resolved", "case_created", "technician_booked", "handover"]


def set_outcome(session, outcome: str) -> None:
    from linecare import db  # here, not at the top: db is not needed by the pure helpers above

    current = session.logged_outcome
    if current is None or OUTCOME_RANK.index(outcome) > OUTCOME_RANK.index(current):
        session.logged_outcome = outcome
        db.update_conversation(session.conv_id, outcome=outcome)
