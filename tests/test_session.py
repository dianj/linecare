"""The session state machine, the identity lock and the two-step confirmations (ARCHITECTURE.md §6.1)."""
from linecare import config
from linecare.session import LOCKED, UNVERIFIED, VERIFIED
from linecare.tools.identity import verify_customer
from mocks.scenarios import DEMO_BY_ACCOUNT

MARIA = DEMO_BY_ACCOUNT["QN-100003"]


def test_successful_verification_fills_the_session(new_session):
    session = new_session()
    result = verify_customer(session, "qn 100003", pin=MARIA.pin)  # spacing and case are normalised
    assert result == {"status": "ok", "verified": True, "first_name": "Мария", "account_status": "active"}
    assert session.state == VERIFIED and session.account_no == "QN-100003" and session.area_code == "PLD-01"


def test_a_suspended_account_points_the_model_to_the_bill(new_session):
    # Found by the evals: Gemma asked "shall I check your bill?" instead of calling get_bill_status.
    sofia = DEMO_BY_ACCOUNT["QN-100006"]
    result = verify_customer(new_session(), sofia.account_no, pin=sofia.pin)
    assert result["account_status"] == "suspended" and "Call get_bill_status now" in result["next_step"]
    assert "next_step" not in verify_customer(new_session(), MARIA.account_no, pin=MARIA.pin)


def test_date_of_birth_works_instead_of_the_pin(new_session):
    session = new_session()
    assert verify_customer(session, "QN-100003", dob=MARIA.dob)["verified"] is True


def test_three_failures_lock_identity_checks_for_15_minutes(new_session, monkeypatch):
    session = new_session()
    first = verify_customer(session, "QN-100003", pin="0000")
    assert first["verified"] is False and first["attempts_left"] == 2
    assert verify_customer(session, "QN-999999", pin=MARIA.pin)["attempts_left"] == 1  # unknown account: same answer
    third = verify_customer(session, "QN-100003", pin="1111")
    assert third["status"] == "blocked" and third["reason"] == "identity_locked" and third["suggest_handover"]
    assert session.state == LOCKED
    # Even the right PIN is refused while locked.
    assert verify_customer(session, "QN-100003", pin=MARIA.pin)["reason"] == "identity_locked"

    monkeypatch.setattr(config, "FROZEN_NOW", "2026-11-20T10:14:59")
    session.begin_turn()
    assert session.state == LOCKED
    monkeypatch.setattr(config, "FROZEN_NOW", "2026-11-20T10:15:00")
    session.begin_turn()
    assert session.state == UNVERIFIED and session.attempts_left() == 3
    assert verify_customer(session, "QN-100003", pin=MARIA.pin)["verified"] is True


def test_malformed_details_are_not_counted_as_attempts(new_session):
    session = new_session()
    for kwargs in ({"pin": "12"}, {"pin": "abcd"}, {"dob": "27.06.1991"}, {}, {"pin": "1590", "dob": MARIA.dob}):
        assert verify_customer(session, "QN-100003", **kwargs)["status"] == "error"
    assert verify_customer(session, "Q-1", pin="1590")["status"] == "error"
    assert session.attempts_left() == 3


def test_a_verified_conversation_cannot_switch_to_another_account(verified):
    session = verified("QN-100003")
    assert verify_customer(session, "QN-100003", pin=MARIA.pin)["note"] == "Already verified."
    other = DEMO_BY_ACCOUNT["QN-100002"]
    assert verify_customer(session, other.account_no, pin=other.pin)["reason"] == "already_verified"
    assert session.account_no == "QN-100003"


def test_pins_and_dates_passed_to_verification_are_remembered_as_secrets(new_session):
    session = new_session()
    verify_customer(session, "QN-100003", pin="0000")
    verify_customer(session, "QN-100003", dob=MARIA.dob)
    assert session.secrets == {"0000", MARIA.dob}


# --- confirmations -----------------------------------------------------------------------

def test_a_confirmation_runs_only_in_a_later_turn_and_only_once(new_session):
    session = new_session()
    session.begin_turn()
    action = session.create_pending("reboot_router", {}, "Restart the router")
    assert session.take_pending("reboot_router", action.confirmation_id) == (None, "confirm_in_next_turn")
    session.begin_turn()
    taken, reason = session.take_pending("reboot_router", action.confirmation_id.lower())
    assert taken is action and reason is None
    assert session.take_pending("reboot_router", action.confirmation_id) == (None, "unknown_or_expired_confirmation")


def test_a_confirmation_is_tied_to_its_tool_and_expires(new_session):
    session = new_session()
    session.begin_turn()
    action = session.create_pending("reboot_router", {}, "Restart the router")
    session.begin_turn()
    assert session.take_pending("book_technician", action.confirmation_id)[1] == "unknown_or_expired_confirmation"
    for _ in range(config.CONFIRMATION_TTL_TURNS):
        session.begin_turn()
    assert session.take_pending("reboot_router", action.confirmation_id)[1] == "unknown_or_expired_confirmation"


def test_a_new_offer_replaces_the_previous_one_for_the_same_tool(new_session):
    session = new_session()
    session.begin_turn()
    first = session.create_pending("reboot_router", {}, "first")
    second = session.create_pending("reboot_router", {}, "second")
    assert list(session.pending) == [second.confirmation_id]
    session.begin_turn()
    assert session.take_pending("reboot_router", first.confirmation_id)[0] is None


def test_the_state_block_names_the_reply_language_and_the_account_status(verified):
    # Found by the evals: Gemma answered an English message in Bulgarian because the name was Cyrillic, and it
    # did not notice a suspended account.
    session = verified("QN-100006")
    session.message_language = "bg"
    block = session.state_block()
    assert "latest message is in Bulgarian: reply in Bulgarian" in block and "account status: suspended" in block


def test_the_state_block_has_facts_but_no_secrets(verified):
    session = verified("QN-100003")
    session.create_pending("reboot_router", {}, "Restart the customer's QN-Box 4 remotely.")
    block = session.state_block()
    assert "identity: verified" in block and "Мария" in block and "Fiber 500" in block
    assert "Restart the customer's QN-Box 4" in block and "Friday 2026-11-20 10:00" in block
    assert MARIA.pin not in block and MARIA.dob not in block and "Demo Street" not in block


def test_after_a_fix_the_state_block_asks_for_the_outcome(verified):
    # Found by the evals: after "it works now" Gemma asked "may I record this as resolved?" instead of recording it.
    session = verified("QN-100007")
    assert "call record_outcome" not in session.state_block()
    session.record_fix("guided_steps")
    assert "call record_outcome" not in session.state_block()  # not before the customer could answer
    session.begin_turn()
    assert "call record_outcome at once" in session.state_block()
    session.outcome = "resolved"
    assert "call record_outcome" not in session.state_block()
