"""Full turns over HTTP with the scripted fake LLM: scenarios end to end, redaction, failures."""
import json
import re

import pytest
from fastapi.testclient import TestClient

import server
from linecare import config, db
from linecare import session as session_module
from linecare.agents import loop
from mocks import service
from mocks.scenarios import DEMO_BY_ACCOUNT

DANIEL, MARIA, SOFIA = DEMO_BY_ACCOUNT["QN-100001"], DEMO_BY_ACCOUNT["QN-100003"], DEMO_BY_ACCOUNT["QN-100006"]


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def fixed_confirmation_ids(monkeypatch):
    monkeypatch.setattr(session_module._secrets, "token_hex", lambda n: "abc123")
    return "CF-ABC123"


def chat(client, text):
    response = client.post("/api/customer/chat", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


def only_session():
    return next(iter(server.sessions.values()))["session"]


def stored_text(conv_id):
    log = db.conversation_log(conv_id)
    return json.dumps(log, ensure_ascii=False)


def test_s1_area_outage_is_explained_without_further_tests(client, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": DANIEL.account_no, "pin": DANIEL.pin})],
                          [("check_outage", {"service": "internet"})],
                          reply="There is an outage in North Gate; it should be fixed by 14:00.")
    data = chat(client, f"No internet since this morning. {DANIEL.account_no}, PIN {DANIEL.pin}")
    assert [c["name"] for c in data["tool_calls"]] == ["verify_customer", "check_outage"]
    assert all(c["status"] == "ok" for c in data["tool_calls"])
    outage_result = json.loads(fake_lm.requests[-1]["messages"][-1]["content"])
    assert outage_result["outage_in_area"] and outage_result["outages"][0]["eta"] == "2026-11-20T14:00:00"
    assert data["state"]["identity"] == "verified" and data["state"]["first_name"] == "Daniel"


def test_s3_frozen_router_is_restarted_only_after_the_customer_agrees(client, fake_lm, fixed_confirmation_ids):
    fake_lm.script_rounds(
        [("verify_customer", {"account_no": MARIA.account_no, "pin": MARIA.pin})],
        [("check_outage", {"service": "internet"})],
        [("get_router_status", {}), ("run_line_test", {})],
        [("reboot_router", {})],
        reply="Рутерът ви е блокирал. Да го рестартирам ли? Интернетът ще спре за около две минути.")
    first = chat(client, f"Нямам интернет. Акаунт {MARIA.account_no}, ПИН {MARIA.pin}")
    assert first["tool_calls"][-1] == {"name": "reboot_router", "arguments": {}, "status": "needs_confirmation"}
    assert first["state"]["pending"] and service.router_status(MARIA.account_no)["verdict"] == "frozen"

    fake_lm.script_rounds([("reboot_router", {"confirmation_id": fixed_confirmation_ids})],
                          [("get_router_status", {})], reply="Готово, рутерът работи. Имате ли интернет вече?")
    second = chat(client, "Да, рестартирайте го.")
    assert [c["status"] for c in second["tool_calls"]] == ["ok", "ok"]
    assert service.router_status(MARIA.account_no)["verdict"] == "ok"
    assert second["state"]["pending"] == []


def test_a_confirmation_in_the_same_turn_as_the_offer_is_refused(client, fake_lm, fixed_confirmation_ids):
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "pin": MARIA.pin})],
                          [("reboot_router", {})], [("reboot_router", {"confirmation_id": fixed_confirmation_ids})],
                          reply="Restarted!")
    data = chat(client, f"{MARIA.account_no} {MARIA.pin} restart my router")
    assert [c["status"] for c in data["tool_calls"]] == ["ok", "needs_confirmation", "blocked"]
    session = only_session()
    assert session.reboots == 0 and service.router_status(MARIA.account_no)["verdict"] == "frozen"


def test_s6_suspended_account_is_explained_from_the_bill(client, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": SOFIA.account_no, "dob": SOFIA.dob})],
                          [("get_bill_status", {})], reply="Your account is suspended because of an overdue bill.")
    data = chat(client, f"Internet stopped. {SOFIA.account_no}, born {SOFIA.dob}")
    bill = json.loads(fake_lm.requests[-1]["messages"][-1]["content"])
    assert bill["account_status"] == "suspended" and bill["balance_eur"] == 39.8
    assert data["tool_calls"][1]["status"] == "ok"


def test_account_tools_before_verification_are_blocked_and_the_model_recovers(client, fake_lm):
    fake_lm.script_rounds([("run_line_test", {})], reply="Could you give me your account number and PIN?")
    data = chat(client, "Test my line please")
    assert data["tool_calls"] == [{"name": "run_line_test", "arguments": {}, "status": "blocked"}]
    assert "identity_required" in fake_lm.requests[-1]["messages"][-1]["content"]


def test_pin_and_birth_date_never_reach_storage_or_later_model_rounds(client, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "pin": "0000"})],
                          reply="That does not match. Could you try your date of birth?")
    chat(client, f"I'm {MARIA.account_no}, my PIN is 0000")
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "dob": MARIA.dob})],
                          [("get_router_status", {})], reply="Thanks, you are verified.")
    chat(client, "Then my birthday: 27.06.1991")
    fake_lm.script(reply="Anything else?")
    chat(client, "What does the status say?")

    def has_secret(text):  # whole tokens only: random ids may contain "0000"
        return bool(re.search(r"(?<![\w-])(0000|27\.06\.1991|1991-06-27)(?![\w-])", text))

    # Requests: turn 1 = [verify, reply], turn 2 = [verify, router status, reply], turn 3 = [reply].
    # The round that calls verify_customer sees the raw value once; every round after it sees it redacted.
    texts = [json.dumps(r["messages"], ensure_ascii=False) for r in fake_lm.requests]
    assert len(texts) == 6
    assert has_secret(texts[0]) and has_secret(texts[2])
    assert not any(has_secret(texts[i]) for i in (1, 3, 4, 5))
    stored = stored_text(only_session().conv_id)
    assert not has_secret(stored)
    assert "[REDACTED]" in stored and MARIA.account_no in stored


def test_the_state_reaches_the_model_and_reasoning_is_off(client, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "pin": MARIA.pin})], reply="Hi Мария")
    chat(client, f"{MARIA.account_no} {MARIA.pin}")
    assert "identity: unverified" in fake_lm.requests[0]["messages"][0]["content"]
    assert "identity: verified" in fake_lm.requests[1]["messages"][0]["content"]  # updated in the same turn
    assert fake_lm.requests[0]["reasoning_effort"] == config.LLM_REASONING_EFFORT == "none"
    assert len(fake_lm.requests[0]["tools"]) == 7


def test_an_llm_outage_returns_a_friendly_message_and_keeps_the_session(client, fake_lm):
    fake_lm.fail_count = loop.llm_client.max_retries + 1
    data = chat(client, "Hello")
    assert data["system_error"] is True and "technical problem" in data["reply"]
    assert only_session().history == []
    fake_lm.script(reply="Back online.")
    assert chat(client, "Hello again")["reply"] == "Back online."


def test_a_slow_model_times_out_cleanly(client, fake_lm, monkeypatch):
    monkeypatch.setattr(loop, "llm_client", loop.llm_client.with_options(timeout=0.3, max_retries=0))
    fake_lm.delay_s = 2
    data = chat(client, "Здравейте")
    assert data["system_error"] is True and "технически проблем" in data["reply"]


def test_the_tool_loop_is_capped(client, fake_lm):
    fake_lm.script_tool_loop("check_outage", {"service": "internet", "city": "Sofia"},
                             rounds=config.MAX_TOOL_ROUNDS + 1)
    data = chat(client, "Is there an outage in Sofia?")
    assert "could not finish" in data["reply"] and len(data["tool_calls"]) == config.MAX_TOOL_ROUNDS + 1


def test_one_turn_at_a_time_per_conversation(client, fake_lm, monkeypatch):
    chat(client, "Hello")
    entry = next(iter(server.sessions.values()))
    monkeypatch.setattr(entry["lock"], "locked", lambda: True)
    response = client.post("/api/customer/chat", json={"text": "Again"})
    assert response.status_code == 409 and response.json()["error_code"] == "busy"


def test_messages_are_validated(client):
    assert client.post("/api/customer/chat", json={"text": ""}).status_code == 422
    assert client.post("/api/customer/chat", json={"text": "x" * (config.MAX_MESSAGE_CHARS + 1)}).status_code == 422
    assert client.post("/api/customer/chat", json={"text": "   "}).status_code == 400


def test_a_model_switch_in_progress_gets_a_polite_answer(client, fake_lm):
    from linecare.lmstudio import model_state
    model_state["switching"] = True
    data = chat(client, "Hi")
    assert data["system_error"] is True and fake_lm.requests == []


def test_reset_starts_a_new_conversation(client, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "pin": MARIA.pin})], reply="Hi")
    before = chat(client, f"{MARIA.account_no} {MARIA.pin}")["state"]["conversation"]
    after = client.post("/api/customer/reset").json()["state"]
    assert after["conversation"] != before and after["identity"] == "unverified"
    assert client.get("/api/customer/state").json()["state"]["conversation"] == after["conversation"]


def test_demo_customers_cover_the_first_scenarios(client):
    customers = client.get("/api/demo-customers").json()["customers"]
    assert [c["scenario"] for c in customers] == ["S1", "S2", "S3", "S4", "S6", "S7"]
    assert customers[2]["pin"] == MARIA.pin


def test_health_and_models(client, fake_lm):
    health = client.get("/api/health").json()
    assert health["llm"] == {"reachable": True, "model_available": True}
    models = client.get("/api/models").json()
    assert models["active"] == config.MODEL_NAME
    assert client.post("/api/model", data={"model": "unknown"}).status_code == 400
    switched = client.post("/api/model", data={"model": "qwen3.8-27b"})
    assert switched.status_code == 200 and fake_lm.loaded == {"qwen3.8-27b"}
