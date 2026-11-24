"""The helpdesk agent's tools, role access, the compensation tool on seeded records, the queue and the assist."""
import json

import pytest

from linecare import db, handover_queue
from linecare.agents.helpdesk import (
    assist_facts,
    credit_question_account,
    parse_card,
    run_assist,
    run_helpdesk_turn,
    tools_for,
)
from linecare.crm import get_crm, set_crm
from linecare.crm.base import CRMUnavailable
from linecare.registry import execute_tool_call
from linecare.staff import OPERATOR, TECHNICIAN, StaffSession, role_for_token
from linecare.tools.cases import create_case
from linecare.tools.diagnostics import get_router_status, run_line_test
from linecare.tools.handover import handover_to_human
from linecare.tools.network import check_outage


@pytest.fixture
def staff():
    def make(role=OPERATOR):
        return StaffSession(conv_id=db.new_conversation("helpdesk", "gemma-4-26b-a4b-it"), staff_id=f"{role}-test",
                            role=role)
    return make


def call(staff_session, name, **arguments):
    return execute_tool_call(staff_session.role, staff_session, name, json.dumps(arguments))


def handover_for(verified, account_no, category, reason, checks=("check_outage", "run_line_test")):
    """A customer conversation that ran checks, opened a case and was handed over."""
    session = verified(account_no)
    from linecare.agents.customer import _remember

    tools = {"check_outage": lambda: check_outage(session, "internet"), "run_line_test": lambda: run_line_test(session),
             "get_router_status": lambda: get_router_status(session)}
    for name in checks:
        _remember(session, name, tools[name]())
    created = create_case(session, category=category, subject="Problem", description="It keeps happening")
    _remember(session, "create_case", created)
    result = handover_to_human(session, reason=reason, customer_view="It keeps happening.")
    return session, result["handover_id"]


# --- roles and tokens ---------------------------------------------------------------------------------------------

def test_demo_tokens_select_the_role():
    assert role_for_token("demo-operator") == OPERATOR and role_for_token("demo-technician") == TECHNICIAN
    assert role_for_token("") is None and role_for_token("guess") is None


def test_technicians_cannot_calculate_credits(staff):
    names = {t["function"]["name"] for t in tools_for(TECHNICIAN)}
    assert "calculate_compensation" not in names and len(names) == 6 and len(tools_for(OPERATOR)) == 7
    result = call(staff(TECHNICIAN), "calculate_compensation", account_no="QN-100009", event="technician_no_show")
    assert result["status"] == "error" and "Unknown tool" in result["message"]


def test_each_role_reads_only_its_internal_documents(staff):
    operator = call(staff(OPERATOR), "search_kb", query="compensation credit outage more than 24 hours")
    technician = call(staff(TECHNICIAN), "search_kb", query="PPPoE configuration after a factory reset QN-Box 4")
    assert {p["doc_id"] for p in operator["passages"] if p["audience"] == "internal"} <= {
        "INT-01", "INT-02", "INT-03", "INT-04", "INT-05", "INT-10"}
    assert "INT-06" in {p["doc_id"] for p in technician["passages"]}
    assert not {p["doc_id"] for p in technician["passages"]} & {"INT-01", "INT-10"}
    assert "INT-06" not in {p["doc_id"] for p in call(staff(OPERATOR), "search_kb", query="PPPoE factory reset",
                                                       scope="technical")["passages"]}
    assert call(staff(), "search_kb", query="x", scope="secret")["status"] == "error"


# --- customer overview and audit ------------------------------------------------------------------------------------

def test_the_overview_has_history_but_no_contact_data_and_is_audited(staff):
    operator = staff()
    assert call(operator, "get_customer_overview", account_no="QN-100009", reason="")["status"] == "error"
    result = call(operator, "get_customer_overview", account_no="qn 100009", reason="handover HO-0001, drops")
    assert result["customer"]["name"] == "Martin Kolev" and result["customer"]["monthly_fee_eur"] == 27.9
    assert [c["case_ref"] for c in result["last_cases"]] == ["00000953", "00000952", "00000951"]
    assert result["missed_visits"][0]["missed_by"] == "quillnet"
    assert [o["outage_id"] for o in result["outages_last_60_days"]] == ["OUT-1987"]
    text = json.dumps(result)
    assert not any(secret in text for secret in ("5903", "1988-02-11", "@example.com", "+359", "Demo Street"))
    assert db.audit_entries("QN-100009")[-1]["reason"] == "handover HO-0001, drops"


def test_the_overview_lists_every_credit_to_check_for_operators_only(staff):
    # Eval hd-credits-all: asked directly, the model checked two of the three events. The list comes from the records.
    result = call(staff(), "get_customer_overview", account_no="QN-100009", reason="angry customer, credits")
    assert result["compensation_checks"] == [
        {"account_no": "QN-100009", "event": "technician_no_show"},
        {"account_no": "QN-100009", "event": "outage", "outage_id": "OUT-1987"},
        {"account_no": "QN-100009", "event": "repeat_fault", "category": "intermittent"}]
    assert "each entry in compensation_checks" in result["message"]
    for args in result["compensation_checks"]:
        assert call(staff(), "calculate_compensation", **args)["status"] == "ok"
    technician = call(staff(TECHNICIAN), "get_customer_overview", account_no="QN-100009", reason="line check, drops")
    assert "compensation_checks" not in technician and "message" not in technician


def test_the_overview_survives_a_crm_outage(staff, monkeypatch):
    monkeypatch.setattr(get_crm(), "list_cases", lambda *a, **k: (_ for _ in ()).throw(CRMUnavailable("down")))
    result = call(staff(), "get_customer_overview", account_no="QN-100009", reason="check during CRM outage")
    assert result["status"] == "ok" and result["last_cases"] == [] and "CRM not reachable" in result["note"]


# --- compensation from the records ---------------------------------------------------------------------------------

@pytest.mark.parametrize("account, arguments, expected", [
    ("QN-100009", {"event": "outage"}, ("yes", 1.86, "C1")),                      # OUT-1987: 33 h, Fiber 500
    ("QN-100002", {"event": "outage"}, ("no", 0.0, "C1")),                        # OUT-1990: planned, 72 h notice
    ("QN-100008", {"event": "outage", "outage_id": "OUT-1995"}, ("no", 0.0, "C1")),  # exactly 24 h
    ("QN-100009", {"event": "technician_no_show"}, ("yes", 10.0, "C2")),
    ("QN-100009", {"event": "repeat_fault", "case_ref": "00000953"}, ("needs_approval", 15.0, "C3")),
    ("QN-100006", {"event": "repeat_fault", "category": "billing"}, ("no", 0.0, "C4")),  # suspended
])
def test_compensation_comes_from_the_records(staff, account, arguments, expected):
    result = call(staff(), "calculate_compensation", account_no=account, **arguments)
    assert (result["eligible"], result["amount_eur"], result["rule_id"]) == expected


def test_compensation_refuses_what_is_not_on_record(staff):
    operator = staff()
    assert "No missed technician visit" in call(operator, "calculate_compensation", account_no="QN-100002",
                                                event="technician_no_show")["message"]
    assert "not in the customer's area" in call(operator, "calculate_compensation", account_no="QN-100002",
                                                event="outage", outage_id="OUT-1987")["message"]
    assert "still active" in call(operator, "calculate_compensation", account_no="QN-100001", event="outage",
                                  outage_id="OUT-2041")["message"]
    assert "Give the case_ref" in call(operator, "calculate_compensation", account_no="QN-100009",
                                       event="repeat_fault")["message"]
    assert call(operator, "calculate_compensation", account_no="QN-999999", event="outage")["status"] == "error"


# --- cases, tickets, handovers -------------------------------------------------------------------------------------

def test_comments_and_tickets_are_written_to_the_case(staff):
    technician = staff(TECHNICIAN)
    assert call(technician, "add_case_comment", case_ref="00000953", text="Line checked")["status"] == "ok"
    ticket = call(technician, "create_internal_ticket", team="network", summary="Recurring drops on this line, "
                  "third case in 60 days.", case_ref="00000953")
    assert ticket["ticket_ref"] == "NT-0001"
    comments = [c["body"] for c in call(technician, "get_case", case_ref="00000953")["case"]["comments"]]
    assert comments[-2:] == ["[technician] Line checked", "Internal ticket NT-0001 to network: Recurring drops on "
                             "this line, third case in 60 days."]
    assert call(technician, "add_case_comment", case_ref="99999999", text="x")["status"] == "error"
    assert call(technician, "create_internal_ticket", team="sales", summary="x" * 20)["status"] == "error"
    assert call(technician, "get_case", case_ref="99999999")["status"] == "error"


def test_the_handover_tool_shows_the_summary_and_a_redacted_transcript(staff, verified):
    _, handover_id = handover_for(verified, "QN-100002", "internet_down", "cannot_resolve")
    conv_id = handover_queue.get_row(handover_id)["conv_id"]
    db.log_turn(conv_id, "user", "QN-100002 [REDACTED] my internet is down")  # stored redacted, as the agent does
    result = call(staff(), "get_handover", handover_id=handover_id.lower())
    # Found in a live run: a field named "status" overwrote the tool's own status ("claimed" instead of "ok").
    assert result["status"] == "ok" and result["handover_status"] == "queued"
    assert result["summary"]["open_case_ref"] == "00001001"
    assert result["transcript"][-1]["text"] == "QN-100002 [REDACTED] my internet is down"
    assert call(staff(), "get_handover", handover_id="HO-9999")["status"] == "error"


# --- customer agent: escalation for repeat contacts (S9) and configuration errors (S5) ----------------------------

def test_a_repeat_contact_is_offered_a_colleague_with_high_priority(verified):
    session = verified("QN-100009")
    _ = run_line_test(session)
    created = create_case(session, category="intermittent", subject="Drops again")
    assert created["suggest_handover"] is True and "repeat_contact" in created["next_step"]
    assert handover_to_human(session, reason="cannot_resolve")["priority"] == "high"
    elena = verified("QN-100002")
    assert "suggest_handover" not in create_case(elena, category="internet_down", subject="Down")


def test_a_wrong_router_configuration_points_to_a_handover(verified):
    status = get_router_status(verified("QN-100005"))
    assert status["verdict"] == "config_error" and "config_required" in status["next_step"]


# --- queue ---------------------------------------------------------------------------------------------------------

def test_claim_and_close_follow_the_rules(staff, verified):
    _, handover_id = handover_for(verified, "QN-100002", "internet_down", "cannot_resolve")
    operator, other = staff(), staff()
    other.staff_id = "operator-other"
    handover_queue.claim(handover_id, operator.staff_id)
    with pytest.raises(handover_queue.QueueError, match="already claimed"):
        handover_queue.claim(handover_id, other.staff_id)
    with pytest.raises(handover_queue.QueueError) as refused:
        handover_queue.close(handover_id, other, "done")
    assert refused.value.code == "not_yours"
    with pytest.raises(handover_queue.QueueError) as refused:
        handover_queue.close(handover_id, operator, "ok")
    assert refused.value.code == "note_required"
    closed = handover_queue.close(handover_id, operator, "Line fault confirmed, visit booked.")
    assert closed["case_ref"] == "00001001"
    assert get_crm().get_case("00001001").comments[-1]["body"].endswith("closed: Line fault confirmed, visit booked.")
    with pytest.raises(handover_queue.QueueError, match="No handover"):
        handover_queue.claim("HO-9999", operator.staff_id)


def test_high_priority_handovers_come_first(verified):
    handover_for(verified, "QN-100002", "internet_down", "cannot_resolve")
    handover_for(verified, "QN-100009", "intermittent", "repeat_contact")
    queue = handover_queue.list_handovers("queued")
    assert [(h["handover_id"], h["priority"]) for h in queue] == [("HO-0002", "high"), ("HO-0001", "normal")]


def test_cases_lost_in_a_crm_outage_are_written_later(verified):
    class DownCRM:
        def __getattr__(self, name):
            raise CRMUnavailable("down")

    working = get_crm()
    session = verified("QN-100002")
    set_crm(DownCRM())
    try:
        assert create_case(session, category="internet_down", subject="Down")["reason"] == "crm_unavailable"
        handover_id = handover_to_human(session, reason="cannot_resolve")["handover_id"]
        assert handover_queue.sync_pending_cases() == 0          # still down: kept for later
    finally:
        set_crm(working)
    assert handover_queue.sync_pending_cases() == 1
    summary = handover_queue.get_row(handover_id)
    case_ref = json.loads(summary["summary_json"])["open_case_ref"]
    assert get_crm().get_case(case_ref).status == "Escalated"
    assert handover_queue.sync_pending_cases() == 0              # written once


# --- the agent and the assist (scripted model) --------------------------------------------------------------------

CARD = """SUMMARY: Third drop case in 60 days; the last visit was missed.
LIKELY CAUSE: An intermittent line fault.
COMPENSATION: 10 EUR for the missed visit (C2); goodwill up to 15 EUR needs approval (C3).
NEXT STEP: Open a network ticket (INT-03 §Repeat faults).
DRAFT REPLY: Draft - review before sending: Dear Martin, we are sorry..."""


def test_the_assist_loads_the_facts_in_code_and_the_model_explains_them(staff, verified, fake_lm):
    _, handover_id = handover_for(verified, "QN-100009", "intermittent", "repeat_contact", checks=("run_line_test",))
    fake_lm.script(reply=CARD)
    result = run_assist(staff(), handover_id, "gemma-4-26b-a4b-it")
    assert result["complete"] and result["card"]["likely_cause"] == "An intermittent line fault."
    names = [c["name"] for c in result["tool_calls"]]
    assert names[:2] == ["get_handover", "get_customer_overview"] and names[-1] == "search_kb"
    credits = {(c["event"], c["rule_id"], c["eligible"], c["amount_eur"]) for c in result["compensation"]}
    assert credits == {("technician_no_show", "C2", "yes", 10.0), ("outage", "C1", "yes", 1.86),
                       ("repeat_fault", "C3", "needs_approval", 15.0)}
    # The internal procedure for the handover reason, searched by code (found in a live run: Gemma missed INT-03).
    assert any(source.startswith("INT-03") for source in result["sources"])
    first = fake_lm.requests[0]["messages"]
    assert "Prepare the assist card for handover" in first[1]["content"] and "English" in first[1]["content"]
    assert [m["role"] for m in first[1:]].count("tool") == 6   # the model saw the facts before answering
    assert len(fake_lm.requests) == 1                          # and answered at once


def test_assist_facts_only_for_events_on_record():
    overview = {"status": "ok", "missed_visits": [], "outages_last_60_days": [],
                "last_cases": [{"category": "wifi"}, {"category": "wifi"}]}
    assert assist_facts({"account_no": "QN-100004"}, overview) == []
    assert assist_facts({"account_no": None}, overview) == []


def test_an_unlabelled_reply_gives_an_incomplete_card():
    assert parse_card("Just text") == {}
    assert parse_card("**Summary:** a\nNEXT STEP: b") == {"summary": "a", "next_step": "b"}


def test_a_credit_question_from_an_operator_loads_every_credit_first(staff, fake_lm):
    # Eval hd-no-bigger-promise: asked "can I promise 25 EUR", Gemma offered to check instead of checking.
    assert credit_question_account("Can I promise qn 100009 25 EUR?") == "QN-100009"
    assert credit_question_account("Какви компенсации се полагат на QN-100009?") == "QN-100009"
    assert credit_question_account("What is the router of QN-100009?") is None
    assert credit_question_account("How do credits work?") is None
    fake_lm.script(reply="10.00, 1.86 and 15.00 (needs approval).")
    result = run_helpdesk_turn(staff(), "Can I promise QN-100009 25 EUR today?", "gemma-4-26b-a4b-it")
    assert [c["name"] for c in result.tool_calls] == ["get_customer_overview"] + ["calculate_compensation"] * 3
    assert all(c["status"] == "ok" for c in result.tool_calls)
    assert [m["role"] for m in fake_lm.requests[0]["messages"]].count("tool") == 4
    assert db.audit_entries("QN-100009")[0]["reason"].startswith("operator asked about credits: Can I promise")
    fake_lm.script(reply="Ask an operator.")
    technician = run_helpdesk_turn(staff(TECHNICIAN), "How much credit should QN-100009 get?", "gemma-4-26b-a4b-it")
    assert technician.tool_calls == []


def test_a_helpdesk_turn_keeps_history_and_logs(staff, fake_lm):
    operator = staff()
    fake_lm.script(("get_case", {"case_ref": "00000953"}), reply="The case is closed.")
    result = run_helpdesk_turn(operator, "What is case 00000953?", "gemma-4-26b-a4b-it")
    assert result.reply == "The case is closed." and result.tool_calls[0]["status"] == "ok"
    assert "your user's role: operator" in fake_lm.requests[0]["messages"][0]["content"]
    assert len(fake_lm.requests[0]["tools"]) == 7
    assert [t["role"] for t in db.conversation_log(operator.conv_id)["turns"]] == ["user", "assistant"]
    assert operator.history[-1] == {"role": "assistant", "content": "The case is closed."}
