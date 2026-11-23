"""verify_customer: the identity gate. Account tools stay blocked until it succeeds."""
from datetime import date

from linecare.session import LOCKED, VERIFIED
from linecare.tools.common import blocked, error, normalise_account_no, ok
from mocks import service as systems


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

    customer = systems.get_customer(account)
    if customer is not None and systems.verify(account, pin=pin, dob=dob):
        session.mark_verified(customer)
        return ok(verified=True, first_name=customer["first_name"])
    # The same answer for an unknown account and a wrong PIN, so the tool cannot be used to probe accounts.
    if session.record_failed_attempt():
        return blocked("identity_locked", "Verification failed too many times; identity checks are locked for "
                       "15 minutes. Do not ask for the PIN again. Offer to connect the customer with a colleague.",
                       suggest_handover=True)
    return ok(verified=False, attempts_left=session.attempts_left(),
              message="The details do not match. Ask the customer to check the account number and PIN (or date of "
                      "birth). Never repeat the PIN or date of birth back.")
