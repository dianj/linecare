"""KPI formulas on a fixed conversation log (ARCHITECTURE.md §12.3)."""
from linecare import db, metrics


def conversation(outcome, scenario=None, model="gemma-4-26b-a4b-it", turns=((900, 100, None),), calls=(),
                 agent="customer", reason=None):
    """A logged conversation: `turns` are (llm_ms, tools_ms, stt_ms) per exchange; calls go on the last reply."""
    conv_id = db.new_conversation(agent, model)
    db.update_conversation(conv_id, outcome=outcome, scenario_tag=scenario)
    for i, (llm_ms, tools_ms, stt_ms) in enumerate(turns):
        db.log_turn(conv_id, "user", "hello", model, stt_ms=stt_ms)
        last = i == len(turns) - 1
        db.log_turn(conv_id, "assistant", "hi", model, llm_ms=llm_ms, tools_ms=tools_ms, rounds=1,
                    tool_calls=[{"name": n, "arguments": {}, "status": s, "summary": m} for n, s, m in calls]
                    if last else ())
    if reason:
        with db.connect() as conn:
            conn.execute("INSERT INTO handovers (handover_id, conv_id, created_at, priority, reason, summary_json) "
                         "VALUES (?, ?, '2026-11-20T10:00:00', 'normal', ?, '{}')", (f"HO-{conv_id}", conv_id, reason))
    return conv_id


def seed_log():
    conversation("resolved", "S3", turns=((1000, 200, None), (800, 100, 1500)),
                 calls=[("reboot_router", "ok", "accepted=True"), ("record_outcome", "ok", "outcome=resolved")])
    conversation("info", None, calls=[("search_help", "ok", "answerable=True")])
    conversation("technician_booked", "S6", turns=((2000, 300, None), (1000, 100, None), (500, 50, None)),
                 calls=[("book_technician", "ok", "appointment_id=AP-1"), ("reboot_router", "ok", "accepted=True")])
    conversation("handover", "S9", calls=[("handover_to_human", "ok", "handover_id=HO-1")], reason="repeat_contact")
    conversation(None, None, calls=[("verify_customer", "blocked", "reason=identity_locked"),
                                    ("get_bill_status", "error", "error")])
    conversation("handover", None, model="qwen3.8-27b", reason="customer_request")
    db.new_conversation("customer", "gemma-4-26b-a4b-it")              # no user turn: not in the base
    conversation(None, agent="helpdesk")                                # staff, not customers


def test_customer_kpis_follow_the_definitions():
    seed_log()
    m = metrics.compute()
    assert m["conversations"] == 6
    assert m["containment_rate"] == round(2 / 6, 3)                    # resolved + info
    assert m["escalation_rate"] == round(2 / 6, 3)
    assert m["escalation_reasons"] == {"repeat_contact": 1, "customer_request": 1}
    assert m["outcomes"] == {"handover": 2, "resolved": 1, "info": 1, "technician_booked": 1, "none": 1}
    assert list(m["by_scenario"]) == ["S3", "S6", "S9", "other"]
    assert m["by_scenario"]["other"] == {"info": 1, "none": 1, "handover": 1}
    assert m["avg_turns_to_resolution"] == 2.0
    assert m["tool_calls"] == 8 and m["tool_error_rate"] == round(1 / 8, 3)
    assert m["blocked_actions"] == {"identity_locked": 1}
    assert m["fixed_remotely"] == 1                                    # the S6 restart was followed by a visit
    assert m["models"] == ["gemma-4-26b-a4b-it", "qwen3.8-27b"]
    assert len(m["recent"]) == 6 and set(m["recent"][0]) >= {"conv_id", "outcome", "scenario_tag", "user_turns"}


def test_latency_per_stage_and_end_to_end():
    seed_log()
    latency = metrics.compute()["latency_ms"]
    # assistant turns: llm 1000, 800 | 900 | 2000, 1000, 500 | 900 | 900 | 900
    assert latency["llm"] == {"p50": 900, "p95": 2000, "n": 9}
    assert latency["stt"] == {"p50": 1500, "p95": 1500, "n": 1}
    assert latency["end_to_end"]["n"] == 9 and latency["end_to_end"]["p95"] == 2400   # voice turn 1500+800+100
    assert latency["tools"]["p50"] == 100


def test_filters_by_model_and_date():
    seed_log()
    assert metrics.compute(model="qwen3.8-27b")["conversations"] == 1
    assert metrics.compute(date_from="2026-11-20", date_to="2026-11-20")["conversations"] == 6
    assert metrics.compute(date_from="2026-11-21")["conversations"] == 0


def test_an_empty_log_gives_no_rates():
    m = metrics.compute()
    assert m["conversations"] == 0 and m["containment_rate"] is None and m["latency_ms"] == {}
    assert metrics.percentile([], 50) is None and metrics.percentile([5], 95) == 5


def test_the_metrics_api_is_for_staff_and_tags_demo_scenarios(fake_lm):
    from fastapi.testclient import TestClient

    import server
    from mocks.scenarios import DEMO_BY_ACCOUNT

    maria = DEMO_BY_ACCOUNT["QN-100003"]
    customer = TestClient(server.app)
    fake_lm.script_rounds([("verify_customer", {"account_no": maria.account_no, "pin": maria.pin})],
                          reply="Thank you, you are verified.")
    customer.post("/api/customer/chat", json={"text": f"{maria.account_no}, PIN {maria.pin}"})

    assert TestClient(server.app).get("/api/metrics").json()["error_code"] == "login_required"
    staff = TestClient(server.app)
    staff.post("/api/staff/login", json={"token": "demo-technician"})
    m = staff.get("/api/metrics").json()
    assert m["conversations"] == 1 and m["recent"][0]["scenario_tag"] == "S3"
    assert staff.get("/api/metrics", params={"from": "20-11-2026"}).status_code == 422
    assert staff.get("/api/metrics", params={"model": "qwen3.8-27b"}).json()["conversations"] == 0
