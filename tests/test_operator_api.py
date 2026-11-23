"""The staff API over HTTP: demo login, role checks, the handover queue, the assist job and the helpdesk chat."""
import time

import pytest
from fastapi.testclient import TestClient
from test_helpdesk import CARD

import server
from linecare.crm import get_crm
from linecare.tools.cases import create_case
from linecare.tools.handover import handover_to_human


@pytest.fixture
def operator():
    client = TestClient(server.app)
    assert client.post("/api/staff/login", json={"token": "demo-operator"}).json()["role"] == "operator"
    return client


@pytest.fixture
def handover(verified):
    session = verified("QN-100009")
    create_case(session, category="intermittent", subject="Drops again")
    return handover_to_human(session, reason="repeat_contact", customer_view="Third time this month.")["handover_id"]


def wait_for_assist(client, handover_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        assist = client.get(f"/api/operator/handovers/{handover_id}/assist").json()
        if assist["status"] != "pending":
            return assist
        time.sleep(0.05)
    raise AssertionError("assist did not finish")


def test_staff_endpoints_need_a_login_and_the_right_role():
    anonymous = TestClient(server.app)
    assert anonymous.get("/api/operator/handovers").json()["error_code"] == "login_required"
    assert anonymous.post("/api/staff/login", json={"token": "nope"}).status_code == 401
    technician = TestClient(server.app)
    technician.post("/api/staff/login", json={"token": "demo-technician"})
    assert technician.get("/api/staff/me").json()["role"] == "technician"
    response = technician.get("/api/operator/handovers")
    assert response.status_code == 403 and response.json()["error_code"] == "forbidden"
    technician.post("/api/staff/logout")
    assert technician.get("/api/staff/me").status_code == 401


def test_claim_assist_comment_and_close(operator, handover, fake_lm):
    queue = operator.get("/api/operator/handovers?status=queued").json()["handovers"]
    assert queue[0]["handover_id"] == handover and queue[0]["priority"] == "high"
    assert queue[0]["first_name"] == "Martin" and queue[0]["open_case_ref"] == "00001001"

    fake_lm.script(reply=CARD)
    claimed = operator.post(f"/api/operator/handovers/{handover}/claim")
    assert claimed.json() == {"handover_id": handover, "status": "claimed", "assist": "pending"}
    assist = wait_for_assist(operator, handover)
    assert assist["status"] == "ready" and assist["card"]["complete"]
    assert {c["rule_id"] for c in assist["card"]["compensation"]} == {"C1", "C2", "C3"}
    assert operator.post(f"/api/operator/handovers/{handover}/claim").status_code == 409

    fake_lm.script(("add_case_comment", {"case_ref": "00001001", "text": "Network ticket requested."}),
                   reply="Comment added.")
    chat = operator.post("/api/helpdesk/chat", json={"text": "Add a comment that we asked the network team."}).json()
    assert chat["tool_calls"][0] == {"name": "add_case_comment", "status": "ok", "summary": chat["tool_calls"][0][
        "summary"]}
    # The helpdesk conversation continues from the assist: the model saw the card request and its answer.
    assert any("Prepare the assist card" in str(m.get("content")) for m in fake_lm.requests[-1]["messages"])

    assert operator.post(f"/api/operator/handovers/{handover}/close", json={"note": "ok"}).status_code == 409
    closed = operator.post(f"/api/operator/handovers/{handover}/close", json={"note": "Ticket NT opened, customer "
                                                                                     "called back."}).json()
    assert closed["status"] == "closed"
    comments = [c["body"] for c in get_crm().get_case("00001001").comments]
    assert comments[-2:] == ["[operator] Network ticket requested.",
                             f"[operator] Handover {handover} closed: Ticket NT opened, customer called back."]
    assert operator.get("/api/operator/handovers?status=queued").json()["handovers"] == []


def test_a_failed_assist_is_reported(operator, handover, fake_lm):
    fake_lm.fail_count = 5
    operator.post(f"/api/operator/handovers/{handover}/claim")
    assert wait_for_assist(operator, handover) == {"status": "failed", "card": None}


def test_unknown_handovers_and_bad_input(operator):
    assert operator.post("/api/operator/handovers/HO-9999/claim").status_code == 404
    assert operator.get("/api/operator/handovers/HO-9999/assist").status_code == 404
    assert operator.post("/api/operator/handovers/HO-9999/close", json={"note": "done done"}).status_code == 404
    assert operator.get("/api/operator/handovers?status=lost").status_code == 400


def test_the_helpdesk_chat_reports_an_llm_outage_and_resets(operator, fake_lm):
    fake_lm.fail_count = 5
    response = operator.post("/api/helpdesk/chat", json={"text": "hello"})
    assert response.status_code == 500 and response.json()["error_code"] == "internal"
    assert operator.post("/api/helpdesk/reset").json() == {"status": "ok"}


def test_staff_can_read_a_transcript_and_it_is_audited(operator, handover):
    row = server.handover_queue.get_row(handover)
    server.db.update_conversation(row["conv_id"], account_no="QN-100009")  # as the customer agent does on verify
    record = operator.get(f"/api/conversations/{row['conv_id']}").json()
    assert record["conversation"]["account_no"] == "QN-100009"
    assert operator.get("/api/conversations/CV-none").status_code == 404
    assert server.db.audit_entries("QN-100009")[-1]["action"] == "read_conversation"
