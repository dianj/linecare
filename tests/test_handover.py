"""Handover: allowed in every state, summary built in code, no secrets, no further actions (ARCHITECTURE.md §11)."""
import json

from linecare import db
from linecare.handover import describe, summary_problems
from linecare.registry import execute_tool_call
from linecare.session import HANDED_OVER
from linecare.tools.cases import create_case
from linecare.tools.diagnostics import get_router_status, run_line_test
from linecare.tools.handover import get_handover, handover_to_human
from linecare.tools.identity import verify_customer
from mocks.scenarios import DEMO_BY_ACCOUNT


def run(session, name, **arguments):
    """Through the dispatcher, with the tool log the customer agent keeps."""
    from linecare.agents.customer import _remember

    result = execute_tool_call("customer", session, name, json.dumps(arguments))
    _remember(session, name, result)
    return result


def test_a_human_can_be_requested_before_verification(new_session):
    session = new_session()
    result = handover_to_human(session, "customer_request", customer_view="Wants a person.")
    assert result["status"] == "ok" and result["handover_id"] == "HO-0001" and result["queue_position"] == 1
    assert session.state == HANDED_OVER
    summary = get_handover("HO-0001")["summary"]
    assert summary["customer"] == {"account_no": None, "first_name": None, "plan": None, "verified": False}
    assert summary["priority"] == "normal" and summary_problems(summary) == []


def test_a_locked_caller_can_still_reach_a_human(new_session):
    session = new_session()
    for pin in ("0000", "1111", "2222"):
        verify_customer(session, "QN-100003", pin=pin)
    result = run(session, "handover_to_human", reason="security_lock", customer_view="Forgot PIN 2222")
    assert result["status"] == "ok" and result["priority"] == "high"
    summary = get_handover(result["handover_id"])["summary"]
    assert "2222" not in json.dumps(summary) and "[REDACTED]" in summary["issue"]["customer_view"]


def test_the_summary_is_built_from_the_tool_log(verified):
    session = verified("QN-100002")
    session.begin_turn()
    run(session, "check_outage", service="internet")
    run(session, "run_line_test")
    run(session, "get_router_status")
    run(session, "search_help", query="technician visit times")
    run(session, "create_case", category="internet_down", subject="Line degraded")
    result = run(session, "handover_to_human", reason="cannot_resolve", sentiment="frustrated",
                 customer_view="No internet since this morning.", suggested_next_step="Book a technician.")
    summary = get_handover(result["handover_id"])["summary"]
    assert [c["tool"] for c in summary["checks"]] == ["check_outage", "run_line_test", "get_router_status"]
    assert summary["checks"][1]["result"].startswith("degraded: SNR 4.1 dB")
    assert summary["actions"] == [{"tool": "create_case", "result": "case 00001001"}]
    assert summary["open_case_ref"] == "00001001" and summary["kb_sources"][0].startswith("PUB-07")
    assert summary["customer"]["first_name"] == "Elena" and summary["issue"]["category"] == "internet_down"
    secrets = {DEMO_BY_ACCOUNT["QN-100002"].pin, DEMO_BY_ACCOUNT["QN-100002"].dob}
    assert summary_problems(summary, secrets) == []
    with db.connect() as conn:
        row = conn.execute("SELECT handover_id, status FROM crm_cases WHERE case_ref = '00001001'").fetchone()
        assert row[:] == ("HO-0001", "Escalated")
        assert conn.execute("SELECT outcome FROM conversations WHERE conv_id = ?",
                            (session.conv_id,)).fetchone()[0] == "handover"


def test_after_a_handover_the_agent_takes_no_further_actions(verified):
    session = verified("QN-100003")
    handover_to_human(session, "customer_request")
    assert run(session, "reboot_router")["reason"] == "handed_over"
    assert run(session, "handover_to_human", reason="customer_request")["reason"] == "handed_over"
    assert "handed over to a colleague (HO-0001)" in session.state_block()


def test_a_repeat_contact_raises_the_priority(verified):
    session = verified("QN-100002")
    create_case(session, "internet_down", "First time")
    with db.connect() as conn:  # the first case was closed; a second one this month
        conn.execute("UPDATE crm_cases SET status = 'Closed'")
    create_case(session, "internet_down", "Second time")
    assert handover_to_human(session, "cannot_resolve")["priority"] == "high"


def test_invalid_reason_and_sentiment(new_session):
    session = new_session()
    assert handover_to_human(session, "bored")["status"] == "error"
    assert handover_to_human(session, "customer_request", sentiment="furious")["status"] == "ok"


def test_completeness_rules_catch_missing_keys_secrets_and_unsupported_cases():
    assert "missing checks" in summary_problems({"handover_id": "HO-1"})
    full = {"checks": [], "actions": [{"tool": "create_case", "result": "case 1"}], "note": "PIN 1590"}
    problems = summary_problems(full, {"1590"})
    assert "secret '1590' in summary" in problems and "a case or visit without any checks" in problems


def test_tool_result_lines():
    assert describe("check_outage", {"outage_in_area": False}) == "no outage in area"
    assert describe("get_bill_status", {"account_status": "suspended", "balance_eur": 19.9,
                                        "overdue_since": "2026-10-20"}) == \
        "account suspended, balance 19.9 EUR, overdue since 2026-10-20"
    assert describe("get_router_status", {"verdict": "rebooting"}) == "rebooting"
    assert describe("search_help", {"passages": []}) == "no article found"
    assert describe("unknown", {"status": "ok"}) == "ok"


def test_diagnostic_lines_from_the_real_tools(verified):
    session = verified("QN-100001")
    assert "(area outage)" in describe("run_line_test", run_line_test(session))
    assert describe("get_router_status", get_router_status(session)).startswith("ok: online True")
