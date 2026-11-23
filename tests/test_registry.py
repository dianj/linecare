"""The dispatcher: allow-lists, the identity gate, argument handling, and it never raises."""
import json

import pytest

from linecare import registry
from linecare.session import HANDED_OVER
from linecare.tool_schemas import CUSTOMER_TOOLS
from linecare.tools.identity import verify_customer

ACCOUNT_TOOLS = ["run_line_test", "get_router_status", "reboot_router", "scan_wifi", "get_bill_status"]


def call(session, name, **arguments):
    return registry.execute_tool_call("customer", session, name, json.dumps(arguments))


def test_every_schema_has_an_implementation_and_back():
    assert {t["function"]["name"] for t in CUSTOMER_TOOLS} == set(registry.CUSTOMER_FUNCTIONS)


@pytest.mark.parametrize("tool", ACCOUNT_TOOLS)
def test_account_tools_are_blocked_until_the_caller_is_verified(new_session, tool):
    result = call(new_session(), tool)
    assert result["status"] == "blocked" and result["reason"] == "identity_required"


def test_public_tools_work_unverified(new_session):
    result = call(new_session(), "check_outage", service="internet", city="Sofia", district="North Gate")
    assert result["status"] == "ok" and result["outage_in_area"] is True


def test_locked_sessions_keep_only_help_and_a_human(new_session):
    session = new_session()
    for pin in ("0000", "1111", "2222"):
        verify_customer(session, "QN-100003", pin=pin)
    for tool, arguments in (("verify_customer", {"account_no": "QN-100003", "pin": "1590"}),
                            ("check_outage", {"service": "internet", "city": "Sofia"}), ("run_line_test", {})):
        assert call(session, tool, **arguments)["reason"] == "identity_locked"


def test_a_handed_over_conversation_takes_no_more_actions(verified):
    session = verified("QN-100003")
    session.state = HANDED_OVER
    assert call(session, "get_router_status")["reason"] == "handed_over"


def test_unknown_tools_bad_json_and_wrong_shapes_are_errors(verified):
    session = verified("QN-100003")
    assert "Unknown tool" in call(session, "delete_account")["message"]
    assert "not valid JSON" in registry.execute_tool_call("customer", session, "scan_wifi", "{oops")["message"]
    assert "JSON object" in registry.execute_tool_call("customer", session, "scan_wifi", "[1, 2]")["message"]
    assert "Invalid arguments" in call(session, "verify_customer", pin="1590")["message"]  # account_no missing
    assert "Unknown tool" in registry.execute_tool_call("helpdesk", session, "scan_wifi", "{}")["message"]


def test_arguments_the_tool_does_not_declare_are_dropped(verified):
    # The model cannot point a tool at another customer by inventing an account_no argument.
    session = verified("QN-100003")
    result = call(session, "get_bill_status", account_no="QN-100006")
    assert result["status"] == "ok" and result["account_status"] == "active"  # Maria's, not the suspended one


def test_an_internal_failure_becomes_a_system_error_without_details(verified, monkeypatch):
    session = verified("QN-100003")

    def broken(_session):
        raise RuntimeError("database is locked at /secret/path")
    monkeypatch.setitem(registry.CUSTOMER_FUNCTIONS, "scan_wifi", broken)
    result = call(session, "scan_wifi")
    assert result["status"] == "system_error" and "/secret/path" not in json.dumps(result)
