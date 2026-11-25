"""Request limits (review T18.A4, T25.S5): a per-IP token bucket on chat, voice, speech and login, and requests without
a cookie that cannot push real sessions out of the session cache."""
from fastapi.testclient import TestClient

import server
from linecare import config, ratelimit


def test_cookieless_polling_does_not_evict_an_active_session(fake_lm):
    customer = TestClient(server.app)
    fake_lm.script(reply="Hello!")
    customer.post("/api/customer/chat", json={"text": "Hi"})
    cookie = customer.cookies.get(server.SESSION_COOKIE)
    assert cookie in server.sessions
    stranger = TestClient(server.app)
    for _ in range(config.MAX_SESSIONS + 100):
        stranger.cookies.clear()
        assert stranger.get("/api/customer/state").status_code == 200
    assert cookie in server.sessions and len(server.sessions) == 1


def test_a_cookieless_reset_makes_no_session():
    client = TestClient(server.app)
    for _ in range(50):
        client.cookies.clear()
        client.post("/api/customer/reset")
    assert len(server.sessions) == 0               # the UI's first reset gets its session with its first message


def test_the_61st_chat_in_a_minute_from_one_address_gets_429(fake_lm, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ratelimit, "monotonic", lambda: clock[0])
    client = TestClient(server.app)
    for _ in range(config.CHAT_PER_MINUTE):
        fake_lm.script(reply="ok")
        assert client.post("/api/customer/chat", json={"text": "Hi"}).status_code == 200
    refused = client.post("/api/customer/chat", json={"text": "Hi"})
    assert refused.status_code == 429 and refused.json()["error_code"] == "rate_limited"
    assert "retry-after" in refused.headers
    clock[0] += 2                                  # the bucket refills at the per-minute rate
    fake_lm.script(reply="ok")
    assert client.post("/api/customer/chat", json={"text": "Hi"}).status_code == 200


def test_staff_login_is_limited_more_tightly(monkeypatch):
    monkeypatch.setattr(ratelimit, "monotonic", lambda: 5000.0)
    client = TestClient(server.app)
    codes = [client.post("/api/staff/login", json={"token": "wrong"}).status_code
             for _ in range(config.LOGIN_PER_MINUTE + 1)]
    assert codes[:-1] == [401] * config.LOGIN_PER_MINUTE and codes[-1] == 429


def test_the_bucket_is_per_address():
    bucket = ratelimit.RateLimiter(per_minute=2)
    assert bucket.allow("a", now=0) and bucket.allow("a", now=0) and not bucket.allow("a", now=0)
    assert bucket.allow("b", now=0)
    assert bucket.allow("a", now=30)                # half a minute later: one more token
