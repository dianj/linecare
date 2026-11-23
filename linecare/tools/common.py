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
        return blocked(reason, "This confirmation is unknown or has expired. Call the tool without "
                               "confirmation_id to make a new offer, and ask the customer.")
    return execute(action.arguments)


# A conversation's outcome only moves up this list: a booked visit stays the outcome even if the customer later
# says thanks, and a handover wins over everything.
OUTCOME_RANK = ["info", "resolved", "case_created", "technician_booked", "handover"]


def set_outcome(session, outcome: str) -> None:
    from linecare import db  # here, not at the top: db is not needed by the pure helpers above

    current = session.logged_outcome
    if current is None or OUTCOME_RANK.index(outcome) > OUTCOME_RANK.index(current):
        session.logged_outcome = outcome
        db.update_conversation(session.conv_id, outcome=outcome)
