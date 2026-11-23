"""The mock enterprise systems as plain functions: what a real operator's CRM, network monitoring, remote
diagnostics, billing and field scheduling would answer. The app's tools call these in-process; `app.py` exposes
them as REST endpoints under /mock.

Never returns a PIN hash or a date of birth.
"""
from datetime import datetime, timedelta

from linecare.db import connect
from mocks import clock
from mocks.seed import pin_matches

REBOOT_SECONDS = 2  # simulated; instant when the clock is frozen (tests, evals)


def _customer_row(conn, account_no):
    return conn.execute(
        "SELECT c.*, a.city, a.district, p.name AS plan_name, p.wan_mode AS plan_wan_mode, p.tv AS plan_tv "
        "FROM sys_customers c JOIN sys_areas a USING (area_code) JOIN sys_plans p USING (plan_id) "
        "WHERE account_no = ?", (account_no,)).fetchone()


# --- customers ---------------------------------------------------------------------------

def get_customer(account_no: str) -> dict | None:
    with connect() as conn:
        row = _customer_row(conn, account_no)
    if row is None:
        return None
    return {"account_no": row["account_no"], "first_name": row["first_name"], "last_name": row["last_name"],
            "language": row["language"], "area_code": row["area_code"], "city": row["city"],
            "district": row["district"], "plan_id": row["plan_id"], "plan_name": row["plan_name"],
            "tv": bool(row["plan_tv"]), "router_model": row["router_model"], "status": row["status"]}


def verify(account_no: str, pin: str = None, dob: str = None) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT pin_hash, dob FROM sys_customers WHERE account_no = ?", (account_no,)).fetchone()
    if row is None:
        return False
    if pin is not None:
        return pin_matches(pin, row["pin_hash"])
    return dob is not None and dob == row["dob"]


# --- network -----------------------------------------------------------------------------

def find_area(city: str = None, district: str = None) -> list[str]:
    """Area codes matching a city and/or district name (case-insensitive)."""
    clauses, params = [], []
    if city:
        clauses.append("lower(city) = lower(?)")
        params.append(city.strip())
    if district:
        clauses.append("lower(district) = lower(?)")
        params.append(district.strip())
    if not clauses:
        return []
    with connect() as conn:
        return [r["area_code"] for r in conn.execute(
            f"SELECT area_code FROM sys_areas WHERE {' AND '.join(clauses)}", params)]


def outages(area_codes: list[str] = None, service: str = None) -> list[dict]:
    query = ("SELECT o.*, a.city, a.district FROM sys_outages o JOIN sys_areas a USING (area_code) "
             "WHERE o.status = 'active'")
    params = []
    if area_codes is not None:
        query += f" AND o.area_code IN ({','.join('?' * len(area_codes))})"
        params += area_codes
    if service:
        query += " AND o.service = ?"
        params.append(service)
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [{"outage_id": r["outage_id"], "service": r["service"], "area": f"{r['district']}, {r['city']}",
             "area_code": r["area_code"], "started_at": r["started_at"], "eta": r["eta"],
             "cause_public": r["cause_public"]} for r in rows]


# --- diagnostics -------------------------------------------------------------------------

def line_test(account_no: str) -> dict | None:
    with connect() as conn:
        customer = _customer_row(conn, account_no)
        line = conn.execute("SELECT * FROM sys_line_state WHERE account_no = ?", (account_no,)).fetchone()
    if customer is None or line is None:
        return None
    in_outage = bool(outages([customer["area_code"]], "internet"))
    if in_outage or not line["sync"]:
        verdict = "no_sync"
    elif line["snr_db"] < 6 or line["errors_15min"] >= 200:
        verdict = "degraded"
    else:
        verdict = "ok"
    return {"verdict": verdict, "sync": bool(line["sync"]) and not in_outage, "snr_db": line["snr_db"],
            "attenuation_db": line["attenuation_db"], "errors_15min": line["errors_15min"],
            "last_drop_at": line["last_drop_at"], "drops_24h": line["drops_24h"], "area_outage": in_outage}


def router_status(account_no: str) -> dict | None:
    now = clock.now()
    with connect() as conn:
        customer = _customer_row(conn, account_no)
        router = conn.execute("SELECT * FROM sys_router_state WHERE account_no = ?", (account_no,)).fetchone()
    if customer is None or router is None:
        return None
    if router["rebooting_until"] and now < datetime.fromisoformat(router["rebooting_until"]):
        back_in = (datetime.fromisoformat(router["rebooting_until"]) - now).seconds
        return {"online": False, "verdict": "rebooting", "back_in_s": back_in, "model": customer["router_model"]}
    in_outage = bool(outages([customer["area_code"]], "internet"))
    if not router["online"]:
        verdict = "offline"
    elif not router["config_ok"]:
        verdict = "config_error"
    elif not router["dhcp_ok"]:
        verdict = "frozen"
    else:
        verdict = "ok"
    return {"online": bool(router["online"]), "model": customer["router_model"], "firmware": router["firmware"],
            "uptime_h": round(router["uptime_s"] / 3600, 1), "wan_ok": bool(router["config_ok"]) and not in_outage,
            "config_ok": bool(router["config_ok"]), "lan_dhcp_ok": bool(router["dhcp_ok"]), "verdict": verdict}


def reboot_router(account_no: str) -> dict | None:
    now = clock.now()
    with connect() as conn:
        router = conn.execute("SELECT * FROM sys_router_state WHERE account_no = ?", (account_no,)).fetchone()
        if router is None:
            return None
        if router["rebooting_until"] and now < datetime.fromisoformat(router["rebooting_until"]):
            return {"accepted": False, "reason": "already_rebooting"}
        seconds = 0 if clock.is_frozen() else REBOOT_SECONDS
        # A reboot clears a frozen router (S3); it does not fix a wrong configuration (S5) or the line (S2).
        conn.execute("UPDATE sys_router_state SET uptime_s = 0, dhcp_ok = 1, online = 1, rebooting_until = ? "
                     "WHERE account_no = ?", ((now + timedelta(seconds=seconds)).isoformat(), account_no))
    return {"accepted": True, "expected_back_in_s": seconds}


def wifi_scan(account_no: str) -> dict | None:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sys_wifi_env WHERE account_no = ? ORDER BY band", (account_no,)).fetchall()
    if not rows:
        return None
    return {"bands": [{"band": f"{r['band']} GHz", "channel": r["channel"], "neighbour_networks":
                       r["neighbour_networks"], "interference": r["interference"]} for r in rows]}


# --- billing -----------------------------------------------------------------------------

def billing(account_no: str) -> dict | None:
    with connect() as conn:
        customer = conn.execute("SELECT status FROM sys_customers WHERE account_no = ?", (account_no,)).fetchone()
        bills = conn.execute("SELECT * FROM sys_bills WHERE account_no = ? ORDER BY period", (account_no,)).fetchall()
    if customer is None:
        return None
    unpaid = [b for b in bills if b["status"] != "paid"]
    overdue = [b for b in bills if b["status"] == "overdue"]
    paid = [b for b in bills if b["status"] == "paid"]
    return {"account_status": customer["status"], "balance_eur": round(sum(b["amount_eur"] for b in unpaid), 2),
            "overdue_since": overdue[0]["due_date"] if overdue else None,
            "last_payment": {"date": paid[-1]["paid_at"], "amount_eur": paid[-1]["amount_eur"]} if paid else None,
            "bills": [{"period": b["period"], "amount_eur": b["amount_eur"], "due_date": b["due_date"],
                       "status": b["status"]} for b in bills]}


# --- scheduling --------------------------------------------------------------------------

def free_slots(area_code: str, days: int = 14, limit: int = 3) -> list[dict]:
    now = clock.now()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sys_tech_slots WHERE area_code = ? AND booked < capacity AND start > ? AND start < ? "
            "ORDER BY start LIMIT ?",
            (area_code, now.isoformat(), (now + timedelta(days=days)).isoformat(), limit)).fetchall()
    return [{"slot_id": r["slot_id"], "start": r["start"], "end": r["end"]} for r in rows]


def get_slot(slot_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sys_tech_slots WHERE slot_id = ?", (str(slot_id).strip().upper(),)).fetchone()
    return dict(row) if row else None


def book_appointment(account_no: str, slot_id: str, reason: str, case_ref: str = None) -> dict | None:
    with connect() as conn:
        slot = conn.execute("SELECT * FROM sys_tech_slots WHERE slot_id = ?", (slot_id,)).fetchone()
        if slot is None or slot["booked"] >= slot["capacity"]:
            return None
        appt_id = f"AP-{conn.execute('SELECT COUNT(*) FROM sys_appointments').fetchone()[0] + 1:04d}"
        conn.execute("INSERT INTO sys_appointments VALUES (?, ?, ?, ?, ?, 'booked')",
                     (appt_id, account_no, slot_id, reason, case_ref))
        conn.execute("UPDATE sys_tech_slots SET booked = booked + 1 WHERE slot_id = ?", (slot_id,))
    return {"appointment_id": appt_id, "slot": {"slot_id": slot_id, "start": slot["start"], "end": slot["end"]}}


def cancel_appointment(appt_id: str) -> bool:
    with connect() as conn:
        appt = conn.execute("SELECT * FROM sys_appointments WHERE appt_id = ? AND status = 'booked'",
                            (appt_id,)).fetchone()
        if appt is None:
            return False
        conn.execute("UPDATE sys_appointments SET status = 'cancelled' WHERE appt_id = ?", (appt_id,))
        conn.execute("UPDATE sys_tech_slots SET booked = booked - 1 WHERE slot_id = ?", (appt["slot_id"],))
    return True
