"""The mock enterprise systems: deterministic seed, scenario signals, no secrets, REST surface."""
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from linecare import config, db
from mocks import seed, service
from mocks.app import router
from mocks.scenarios import DEMO_BY_ACCOUNT, DEMO_CUSTOMERS


def table_fingerprint(path):
    with db.connect(path) as conn:
        rows = [tuple(r) for table in ("sys_customers", "sys_line_state", "sys_router_state", "sys_bills")
                for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def test_seed_is_deterministic(tmp_path):
    for name in ("a.db", "b.db"):
        with db.connect(tmp_path / name) as conn:
            seed.seed(conn)
    assert table_fingerprint(tmp_path / "a.db") == table_fingerprint(tmp_path / "b.db")


def test_seed_sizes_and_pins_are_hashed():
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sys_customers").fetchone()[0] == seed.CUSTOMER_COUNT
        assert conn.execute("SELECT COUNT(*) FROM sys_areas").fetchone()[0] == 12
        hashes = [r[0] for r in conn.execute("SELECT pin_hash FROM sys_customers")]
        stored = {c.account_no: conn.execute("SELECT pin_hash FROM sys_customers WHERE account_no = ?",
                                             (c.account_no,)).fetchone()[0] for c in DEMO_CUSTOMERS}
    assert all(h.count("$") == 1 and len(h.split("$")[1]) == 64 for h in hashes)  # salt$sha256, never the PIN
    assert len(set(hashes)) == len(hashes)  # salted: equal PINs would still differ
    assert all(seed.pin_matches(c.pin, stored[c.account_no]) for c in DEMO_CUSTOMERS)


def test_random_customers_avoid_outage_areas_so_healthy_means_healthy():
    with db.connect() as conn:
        rows = conn.execute("SELECT area_code FROM sys_customers WHERE scenario = 'healthy'").fetchall()
    assert {r[0] for r in rows}.isdisjoint({"SOF-02", "PLD-03"})


@pytest.mark.parametrize("account, check", [
    ("QN-100001", lambda: service.line_test("QN-100001")["verdict"] == "no_sync"
     and service.outages(["SOF-02"], "internet")),
    ("QN-100002", lambda: service.line_test("QN-100002")["verdict"] == "degraded"
     and service.router_status("QN-100002")["verdict"] == "ok"),
    ("QN-100003", lambda: service.router_status("QN-100003")["verdict"] == "frozen"
     and service.line_test("QN-100003")["verdict"] == "ok"),
    ("QN-100004", lambda: [b["interference"] for b in service.wifi_scan("QN-100004")["bands"]] == ["high", "low"]),
    ("QN-100005", lambda: service.router_status("QN-100005")["verdict"] == "config_error"),
    ("QN-100006", lambda: service.billing("QN-100006")["account_status"] == "suspended"
     and service.billing("QN-100006")["overdue_since"]),
    ("QN-100007", lambda: service.line_test("QN-100007")["verdict"] == "ok"
     and service.router_status("QN-100007")["verdict"] == "ok"),
    ("QN-100008", lambda: service.line_test("QN-100008")["drops_24h"] >= 3),
    ("QN-100010", lambda: service.outages(["PLD-03"], "tv") and not service.outages(["PLD-03"], "internet")),
])
def test_each_scenario_shows_its_signals(account, check):
    assert DEMO_BY_ACCOUNT[account].scenario
    assert check()


def test_a_reboot_clears_a_frozen_router_but_not_a_wrong_configuration():
    assert service.reboot_router("QN-100003") == {"accepted": True, "expected_back_in_s": 0}  # frozen clock: instant
    assert service.router_status("QN-100003")["verdict"] == "ok"
    service.reboot_router("QN-100005")
    assert service.router_status("QN-100005")["verdict"] == "config_error"


def test_a_reboot_takes_time_on_the_real_clock(monkeypatch):
    monkeypatch.setattr(config, "FROZEN_NOW", "")
    assert service.reboot_router("QN-100003")["expected_back_in_s"] == service.REBOOT_SECONDS
    assert service.router_status("QN-100003")["verdict"] == "rebooting"
    assert service.reboot_router("QN-100003") == {"accepted": False, "reason": "already_rebooting"}


def test_verification_by_pin_or_date_of_birth():
    demo = DEMO_BY_ACCOUNT["QN-100003"]
    assert service.verify(demo.account_no, pin=demo.pin)
    assert service.verify(demo.account_no, dob=demo.dob)
    assert not service.verify(demo.account_no, pin="0000")
    assert not service.verify("QN-999999", pin=demo.pin)
    assert not service.verify(demo.account_no)


def test_booking_takes_a_slot_and_cancelling_returns_it():
    slot = service.free_slots("SOF-01", limit=1)[0]
    first = service.book_appointment("QN-100002", slot["slot_id"], "line fault")
    service.book_appointment("QN-100011", slot["slot_id"], "other")  # capacity 2
    assert service.book_appointment("QN-100012", slot["slot_id"], "full") is None
    assert slot["slot_id"] not in [s["slot_id"] for s in service.free_slots("SOF-01", limit=5)]
    assert service.cancel_appointment(first["appointment_id"])
    assert not service.cancel_appointment(first["appointment_id"])
    assert slot["slot_id"] in [s["slot_id"] for s in service.free_slots("SOF-01", limit=5)]


# --- REST surface ------------------------------------------------------------------------

@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_customer_profile_never_contains_pin_or_birth_date(api):
    profile = api.get("/mock/customers/QN-100003").json()
    assert profile["first_name"] == "Мария"
    assert not {"pin", "pin_hash", "dob"} & set(profile)
    assert DEMO_BY_ACCOUNT["QN-100003"].dob not in str(profile)
    assert api.get("/mock/customers/QN-999999").status_code == 404


def test_mock_endpoints(api):
    demo = DEMO_BY_ACCOUNT["QN-100002"]
    assert api.post("/mock/customers/verify", json={"account_no": demo.account_no, "pin": demo.pin}).json() == {
        "verified": True}
    assert api.post("/mock/customers/verify", json={"account_no": demo.account_no, "pin": "12"}).status_code == 422
    outages = api.get("/mock/network/outages", params={"area_code": "SOF-02", "service": "internet"}).json()
    assert outages["outages"][0]["outage_id"] == "OUT-2041"
    assert api.post("/mock/diagnostics/QN-100002/line-test").json()["verdict"] == "degraded"
    assert api.get("/mock/diagnostics/QN-100003/router").json()["verdict"] == "frozen"
    assert api.post("/mock/diagnostics/QN-100003/router/reboot").json()["accepted"] is True
    assert api.get("/mock/diagnostics/QN-100004/wifi-scan").json()["bands"][0]["interference"] == "high"
    assert api.get("/mock/billing/QN-100006").json()["account_status"] == "suspended"
    slot = api.get("/mock/scheduling/slots", params={"area_code": "SOF-01"}).json()["slots"][0]
    booked = api.post("/mock/scheduling/appointments", json={"account_no": demo.account_no,
                                                            "slot_id": slot["slot_id"], "reason": "line fault"})
    assert booked.status_code == 200
    assert api.delete(f"/mock/scheduling/appointments/{booked.json()['appointment_id']}").json() == {"cancelled": True}
    assert api.delete("/mock/scheduling/appointments/AP-9999").status_code == 404
    assert api.post("/mock/diagnostics/QN-999999/line-test").status_code == 404


def test_mock_systems_are_documented_in_openapi(api):
    paths = api.get("/openapi.json").json()["paths"]
    assert {"/mock/customers/{account_no}", "/mock/diagnostics/{account_no}/line-test",
            "/mock/scheduling/appointments"} <= set(paths)
