"""A stalled model (review T18.A8): the SDK default was 2 retries of 120 s each, so one turn could hold the customer
for six minutes. Now one retry at most and a time budget per turn; past it the customer gets the friendly
"technical problem" reply."""
import time

from fastapi.testclient import TestClient

import server
from linecare import config
from linecare.agents import loop


def test_the_client_retries_at_most_once():
    assert loop.llm_client.max_retries == 1


def test_a_slow_model_ends_within_the_turn_budget(fake_lm, monkeypatch):
    monkeypatch.setattr(config, "TURN_BUDGET_SECONDS", 1.0)
    fake_lm.delay_s = 3                                   # every completion takes longer than the whole budget
    fake_lm.script(reply="too late")
    started = time.perf_counter()
    data = TestClient(server.app).post("/api/customer/chat", json={"text": "Hello"}).json()
    elapsed = time.perf_counter() - started
    assert data["system_error"] is True and data["reply"] == server.TECHNICAL_PROBLEM["en"]
    assert elapsed < 2.9                                  # not the model's 3 s, and no retry on top


def test_the_budget_covers_all_rounds_of_a_turn(fake_lm, monkeypatch):
    monkeypatch.setattr(config, "TURN_BUDGET_SECONDS", 1.5)
    fake_lm.delay_s = 0.6
    fake_lm.script_rounds([("check_outage", {"service": "internet"})], [("check_outage", {"service": "tv"})],
                          [("check_outage", {"service": "internet"})], reply="done")
    started = time.perf_counter()
    data = TestClient(server.app).post("/api/customer/chat", json={"text": "Is there an outage?"}).json()
    assert data["system_error"] is True and time.perf_counter() - started < 2.4
