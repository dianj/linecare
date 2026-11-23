"""create_case, book_technician and record_outcome: their rules on the seeded scenarios."""
import json

import pytest

from linecare import crm, db
from linecare.agents.customer import _remember
from linecare.registry import execute_tool_call
from linecare.tools.cases import create_case
from linecare.tools.outcome import record_outcome
from linecare.tools.scheduling import book_technician
from mocks import service as systems


def run(session, name, **arguments):
    result = execute_tool_call("customer", session, name, json.dumps(arguments))
    _remember(session, name, result)
    return result


def diagnosed(verified, account_no):
    session = verified(account_no)
    run(session, "run_line_test")
    run(session, "get_router_status")
    return session


def outcome_in_db(session):
    with db.connect() as conn:
        return conn.execute("SELECT outcome FROM conversations WHERE conv_id = ?", (session.conv_id,)).fetchone()[0]


# --- create_case -------------------------------------------------------------------------

def test_a_case_carries_the_checks_from_the_tool_log(verified):
    session = diagnosed(verified, "QN-100002")
    result = create_case(session, "internet_down", "Line degraded", "No internet since morning",
                         diagnostics_summary="SNR low, PIN 7305 given")
    assert result == {"status": "ok", "case_ref": "00001001", "already_open": False, "message": result["message"]}
    detail = crm.get_crm().get_case("00001001")
    assert "run_line_test: degraded: SNR 4.1 dB" in detail.diagnostics and "7305" not in detail.diagnostics
    assert session.open_case_ref == "00001001" and outcome_in_db(session) == "case_created"
    assert "open case: 00001001 (internet_down)" in session.state_block()


def test_the_same_issue_returns_the_open_case(verified):
    session = verified("QN-100002")
    create_case(session, "internet_down", "First")
    again = create_case(session, "internet_down", "Again")
    assert again["case_ref"] == "00001001" and again["already_open"] is True


def test_no_case_for_a_problem_a_known_outage_explains(verified):
    session = verified("QN-100001")  # S1: internet outage in the area
    assert create_case(session, "internet_down", "No internet")["reason"] == "known_outage"
    assert create_case(session, "billing", "Question about the bill")["status"] == "ok"


def test_invalid_case_arguments(verified):
    session = verified("QN-100002")
    assert create_case(session, "broken", "x")["status"] == "error"
    assert create_case(session, "wifi", " ")["status"] == "error"
    unverified = execute_tool_call("customer", session.__class__(conv_id="x"), "create_case",
                                   json.dumps({"category": "wifi", "subject": "x"}))
    assert unverified["reason"] == "identity_required"


def test_a_crm_failure_is_reported_and_remembered(verified, monkeypatch):
    class DownCRM:
        def upsert_contact(self, contact):
            raise ConnectionError("CRM down")

    monkeypatch.setattr(crm, "_crm", DownCRM())
    session = verified("QN-100002")
    result = create_case(session, "internet_down", "No internet")
    assert result["status"] == "error" and result["reason"] == "crm_unavailable"
    assert session.pending_crm_sync is True and session.open_case_ref is None


# --- book_technician ---------------------------------------------------------------------

def test_a_line_fault_gets_a_visit_in_two_steps(verified):
    session = diagnosed(verified, "QN-100002")
    create_case(session, "internet_down", "Line degraded")
    offer = run(session, "book_technician")
    assert offer["status"] == "ok" and len(offer["slots"]) == 3
    first = offer["slots"][0]
    assert first == {"slot_id": "SL-SOF-01-1121-08", "label": "Saturday 2026-11-21, 08:00-10:00"}

    pending = run(session, "book_technician", slot_id=first["slot_id"])
    assert pending["status"] == "needs_confirmation" and "case 00001001" in pending["summary"]
    same_turn = run(session, "book_technician", confirmation_id=pending["confirmation_id"])
    assert same_turn["reason"] == "confirm_in_next_turn"

    session.begin_turn()
    done = run(session, "book_technician", confirmation_id=pending["confirmation_id"])
    assert done["status"] == "ok" and done["appointment_id"] == "AP-0001"
    assert systems.get_slot(first["slot_id"])["booked"] == 1
    assert crm.get_crm().get_case("00001001").comments[0]["body"].startswith("Technician visit booked: Saturday")
    assert outcome_in_db(session) == "technician_booked" and "technician booked: Saturday" in session.state_block()
    assert run(session, "book_technician")["reason"] == "already_booked"


@pytest.mark.parametrize("account_no, reason", [
    ("QN-100001", "area_outage"),        # S1
    ("QN-100003", "no_fault_found"),     # S3: frozen router, a restart fixes it
    ("QN-100004", "no_fault_found"),     # S4: Wi-Fi only
    ("QN-100006", "account_suspended"),  # S6
    ("QN-100007", "no_fault_found"),     # S7
])
def test_no_visit_without_a_fault_on_our_side(verified, account_no, reason):
    session = diagnosed(verified, account_no)
    assert book_technician(session)["reason"] == reason


def test_a_visit_needs_checks_and_a_case(verified):
    session = verified("QN-100002")
    assert book_technician(session)["reason"] == "diagnostics_required"
    run(session, "run_line_test")
    assert book_technician(session)["reason"] == "case_required"


@pytest.mark.parametrize("slot_id", ["SL-NOPE", "SL-PLD-01-1121-08", "SL-SOF-01-1120-08"])
def test_unknown_foreign_or_past_slots_are_refused(verified, slot_id):
    # SL-PLD-01 is another area; 2026-11-20 08:00 is before the frozen "now" (10:00).
    session = diagnosed(verified, "QN-100002")
    create_case(session, "internet_down", "Line degraded")
    assert book_technician(session, slot_id=slot_id)["status"] == "error"


def test_a_slot_taken_meanwhile_is_reported(verified):
    session = diagnosed(verified, "QN-100002")
    create_case(session, "internet_down", "Line degraded")
    pending = book_technician(session, slot_id="SL-SOF-01-1121-08")
    with db.connect() as conn:
        conn.execute("UPDATE sys_tech_slots SET booked = capacity WHERE slot_id = 'SL-SOF-01-1121-08'")
    session.begin_turn()
    assert book_technician(session, confirmation_id=pending["confirmation_id"])["reason"] == "slot_taken"


# --- record_outcome ----------------------------------------------------------------------

def test_an_outcome_needs_a_fix_in_an_earlier_turn(verified):
    session = verified("QN-100003")
    assert record_outcome(session, "resolved")["reason"] == "no_fix_to_confirm"
    offer = run(session, "reboot_router")
    session.begin_turn()
    run(session, "reboot_router", confirmation_id=offer["confirmation_id"])
    assert record_outcome(session, "resolved")["reason"] == "no_fix_to_confirm"  # same turn as the restart
    session.begin_turn()  # "yes, it works now"
    assert record_outcome(session, "resolved")["status"] == "ok"
    assert session.outcome == "resolved" and outcome_in_db(session) == "resolved"


def test_not_resolved_keeps_the_conversation_going(verified):
    session = verified("QN-100007")
    run(session, "search_help", query="forget the Wi-Fi network and restart the device")
    session.begin_turn()  # "да, пак не работи"
    result = record_outcome(session, "not_resolved")
    assert result["status"] == "ok" and "offer a case or a colleague" in result["message"]
    assert outcome_in_db(session) is None
    assert record_outcome(session, "maybe")["status"] == "error"


def test_the_logged_outcome_only_moves_up(verified):
    session = diagnosed(verified, "QN-100002")
    create_case(session, "internet_down", "Line degraded")
    session.record_fix("guided_steps")
    session.begin_turn()
    record_outcome(session, "resolved")
    assert outcome_in_db(session) == "case_created"
