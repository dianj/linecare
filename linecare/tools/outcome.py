"""record_outcome: whether a fix worked, in the customer's words. Gated in code, not by keyword matching.

Allowed only after a fixing action succeeded (remote restart, guided steps from a technical help article) in an
EARLIER turn than the current one, so the customer has had a chance to answer. "Да, пак не работи" ("yes, it
still does not work") is the model's call to read, not a regex's.
"""
from linecare.tools.common import blocked, error, ok, set_outcome


def record_outcome(session, outcome: str, note: str = "") -> dict:
    if outcome not in ("resolved", "not_resolved"):
        return error("outcome must be 'resolved' or 'not_resolved'")
    if not any(fix["turn"] < session.turn for fix in session.fixes):
        return blocked("no_fix_to_confirm", "Record an outcome only after a fix (a restart or guided steps) and the "
                       "customer's answer to 'does it work now?' in a later message.")
    session.outcome = outcome
    if outcome == "resolved":
        set_outcome(session, "resolved")
        return ok(outcome=outcome, message="Recorded. Close the conversation politely.")
    return ok(outcome=outcome, message="Recorded. Continue the diagnosis, or offer a case or a colleague.")
