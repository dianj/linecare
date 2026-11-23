"""verify_customer: the identity gate. Account tools stay blocked until it succeeds."""
import logging
from collections import Counter
from datetime import date

from linecare import db
from linecare.crm import get_crm
from linecare.policy.escalation import REPEAT_CASES, REPEAT_WINDOW_DAYS
from linecare.session import LOCKED, VERIFIED
from linecare.tools.common import blocked, error, normalise_account_no, ok
from mocks import service as systems

log = logging.getLogger("linecare.tools")


def repeat_categories(account_no: str) -> dict:
    """Case categories with REPEAT_CASES or more cases in the last REPEAT_WINDOW_DAYS days (escalation policy, S9)."""
    try:
        cases = get_crm().list_cases(account_no, REPEAT_WINDOW_DAYS)
    except Exception:
        log.warning("CRM history not available at verification")
        return {}
    return {category: n for category, n in Counter(c.category for c in cases).items() if n >= REPEAT_CASES}


def verify_customer(session, account_no: str, pin: str = None, dob: str = None) -> dict:
    if session.state == LOCKED:
        return blocked("identity_locked", "Identity checks are locked after too many failed attempts. Offer to "
                       "connect the customer with a colleague.", suggest_handover=True)
    account = normalise_account_no(account_no)
    if account is None:
        return error("account_no must look like QN-123456. Ask the customer to read it from their bill.")
    if session.state == VERIFIED:
        if account == session.account_no:
            return ok(verified=True, first_name=session.first_name, note="Already verified.")
        return blocked("already_verified", "This conversation is verified for another account. To help with a "
                       "different account the customer must start a new conversation.")

    pin = str(pin).strip() if pin not in (None, "") else None
    dob = str(dob).strip() if dob not in (None, "") else None
    if (pin is None) == (dob is None):
        return error("Give exactly one of pin (4 digits) or dob (YYYY-MM-DD).")
    if pin is not None and not (pin.isdigit() and len(pin) == 4):
        return error("pin must be exactly 4 digits.")
    if dob is not None:
        try:
            date.fromisoformat(dob)
        except ValueError:
            return error("dob must be a date in the form YYYY-MM-DD.")
    session.secrets.add(pin or dob)
    if db.account_locked_until(account):
        # Locked by failures in this or other conversations: no check at all, so a lock cannot be worked around
        # by starting a new conversation. Same message for every account number.
        return blocked("identity_locked", "Identity checks for this account number are locked for a while after "
                       "too many failed attempts. Do not ask for the PIN again. Offer to connect the customer with a "
                       "colleague.", suggest_handover=True)

    customer = systems.get_customer(account)
    if customer is not None and systems.verify(account, pin=pin, dob=dob):
        db.clear_account_failures(account)
        session.mark_verified(customer)
        session.repeat_categories = repeat_categories(account)
        result = ok(verified=True, first_name=customer["first_name"], account_status=customer["status"])
        if session.repeat_categories:
            # Found by the evals: with the history only in create_case, Gemma offered a colleague late or never.
            history = ", ".join(f"{n} cases about {c}" for c, n in session.repeat_categories.items())
            result["crm_history"] = f"{history} in the last {REPEAT_WINDOW_DAYS} days"
            result["next_step"] = ("If the customer's problem is the same one, this is a repeat contact: say you can "
                                   "see the earlier cases and offer a colleague now (handover_to_human with reason "
                                   "repeat_contact once they agree). You may run the remote checks first.")
        if customer["status"] == "suspended":
            # Found by the evals: Gemma asked "shall I check your bill?" instead of checking it.
            result["next_step"] = ("The account is suspended. Call get_bill_status now, in this turn, and explain "
                                   "the overdue balance; no technical tests are needed.")
        return result
    # The same answer for an unknown account and a wrong PIN, so the tool cannot be used to probe accounts.
    account_locked = db.record_account_failure(account)
    if session.record_failed_attempt() or account_locked:
        if session.state != LOCKED:
            session.lock()
        return blocked("identity_locked", "Verification failed too many times; identity checks are locked for "
                       "15 minutes. Do not ask for the PIN again. Offer to connect the customer with a colleague.",
                       suggest_handover=True)
    return ok(verified=False, attempts_left=session.attempts_left(),
              message="The details do not match. Ask the customer to check the account number and PIN (or date of "
                      "birth). Never repeat the PIN or date of birth back.")
