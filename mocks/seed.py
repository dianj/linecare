"""Deterministic seed data for the mock systems (random seed 42; times relative to the clock in mocks/clock.py).

Run `python -m mocks.seed` to rebuild the mock tables in the configured database.
"""
import hashlib
import random
from datetime import timedelta

from mocks import clock
from mocks.scenarios import DEMO_CUSTOMERS, OUTAGE_AREAS

SEED = 42
CUSTOMER_COUNT = 200
PIN_HASH_ITERATIONS = 10_000  # enough for a demo; keeps seeding fast

AREAS = [
    ("SOF-01", "Sofia", "Lime Hill"), ("SOF-02", "Sofia", "North Gate"), ("SOF-03", "Sofia", "Old Mill"),
    ("SOF-04", "Sofia", "Riverside"), ("SOF-05", "Sofia", "Cedar Park"),
    ("PLD-01", "Plovdiv", "Three Hills"), ("PLD-02", "Plovdiv", "Market Quarter"), ("PLD-03", "Plovdiv", "Canal Side"),
    ("PLD-04", "Plovdiv", "Orchard Lane"),
    ("VAR-01", "Varna", "Sea Garden"), ("VAR-02", "Varna", "Harbour View"), ("VAR-03", "Varna", "Lighthouse"),
]
# plan_id, name, down, up, tv, price per month (EUR), contract months, WAN mode the router must use
PLANS = [
    ("DSL50", "DSL 50", 50, 10, 0, 17.90, 12, "pppoe"),
    ("FIB100", "Fiber 100", 100, 50, 0, 19.90, 12, "dhcp"),
    ("FIB500", "Fiber 500", 500, 100, 0, 27.90, 24, "dhcp"),
    ("FIB1000TV", "Fiber 1000 + TV", 1000, 200, 1, 39.90, 24, "dhcp"),
]
ROUTERS = {"QN-Box 3": "3.8.2", "QN-Box 4": "4.2.1", "QN-Mesh Duo": "1.6.0"}

FIRST_NAMES = ["Alex", "Nina", "Viktor", "Laura", "Simon", "Ralitsa", "Boris", "Clara", "Ivo", "Hannah",
               "Иван", "Елица", "Петър", "Десислава", "Христо", "Йоана", "Стефан", "Милена", "Калин", "Ралица"]
LAST_NAMES = ["Hart", "Popov", "Fischer", "Todorova", "Bennett", "Angelova", "Keller", "Marinov",
              "Иванов", "Георгиева", "Николов", "Петкова", "Василев", "Стоянова", "Колев", "Михайлова"]

SCHEMA = """
DROP TABLE IF EXISTS sys_appointments; DROP TABLE IF EXISTS sys_tech_slots; DROP TABLE IF EXISTS sys_bills;
DROP TABLE IF EXISTS sys_wifi_env; DROP TABLE IF EXISTS sys_router_state; DROP TABLE IF EXISTS sys_line_state;
DROP TABLE IF EXISTS sys_outages; DROP TABLE IF EXISTS sys_customers; DROP TABLE IF EXISTS sys_plans;
DROP TABLE IF EXISTS sys_areas;
CREATE TABLE sys_areas (area_code TEXT PRIMARY KEY, city TEXT, district TEXT);
CREATE TABLE sys_plans (plan_id TEXT PRIMARY KEY, name TEXT, speed_down_mbps INTEGER, speed_up_mbps INTEGER,
    tv INTEGER, price_eur REAL, contract_months INTEGER, wan_mode TEXT);
CREATE TABLE sys_customers (account_no TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, dob TEXT, pin_hash TEXT,
    phone TEXT, email TEXT, language TEXT, address TEXT, area_code TEXT REFERENCES sys_areas(area_code),
    plan_id TEXT REFERENCES sys_plans(plan_id), router_model TEXT, router_serial TEXT,
    status TEXT CHECK (status IN ('active', 'suspended')), since TEXT, scenario TEXT);
CREATE TABLE sys_outages (outage_id TEXT PRIMARY KEY, area_code TEXT, service TEXT, started_at TEXT, eta TEXT,
    status TEXT, cause_public TEXT);
CREATE TABLE sys_line_state (account_no TEXT PRIMARY KEY, sync INTEGER, attenuation_db REAL, snr_db REAL,
    errors_15min INTEGER, last_drop_at TEXT, drops_24h INTEGER);
CREATE TABLE sys_router_state (account_no TEXT PRIMARY KEY, online INTEGER, uptime_s INTEGER, firmware TEXT,
    wifi_band_2g_channel INTEGER, wifi_band_5g_channel INTEGER, dhcp_ok INTEGER, wan_mode TEXT, config_ok INTEGER,
    rebooting_until TEXT);
CREATE TABLE sys_wifi_env (account_no TEXT, band TEXT, channel INTEGER, neighbour_networks INTEGER,
    interference TEXT, PRIMARY KEY (account_no, band));
CREATE TABLE sys_bills (bill_id TEXT PRIMARY KEY, account_no TEXT, period TEXT, amount_eur REAL, due_date TEXT,
    paid_at TEXT, status TEXT CHECK (status IN ('paid', 'due', 'overdue')));
CREATE TABLE sys_tech_slots (slot_id TEXT PRIMARY KEY, area_code TEXT, start TEXT, end TEXT, capacity INTEGER,
    booked INTEGER);
CREATE TABLE sys_appointments (appt_id TEXT PRIMARY KEY, account_no TEXT, slot_id TEXT, reason TEXT, case_ref TEXT,
    status TEXT);
"""


def hash_pin(pin: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), PIN_HASH_ITERATIONS).hex()
    return f"{salt}${digest}"


def pin_matches(pin: str, stored: str) -> bool:
    import hmac

    salt, _, _ = stored.partition("$")
    return hmac.compare_digest(hash_pin(pin, salt), stored)


def _healthy_line(rng):
    return dict(sync=1, attenuation_db=round(rng.uniform(8, 25), 1), snr_db=round(rng.uniform(18, 32), 1),
                errors_15min=rng.randint(0, 15), last_drop_at=None, drops_24h=0)


def _healthy_router(rng, model, wan_mode):
    return dict(online=1, uptime_s=rng.randint(3, 40) * 86400, firmware=ROUTERS[model],
                wifi_band_2g_channel=rng.choice([1, 6, 11]), wifi_band_5g_channel=rng.choice([36, 44, 100]),
                dhcp_ok=1, wan_mode=wan_mode, config_ok=1, rebooting_until=None)


def _apply_scenario(scenario, line, router, wifi, now):
    if scenario == "S2":
        line.update(snr_db=4.1, errors_15min=312, attenuation_db=41.5)
    elif scenario == "S3":
        router.update(uptime_s=97 * 86400, dhcp_ok=0)
    elif scenario == "S4":
        wifi["2.4"].update(channel=6, neighbour_networks=23, interference="high")
    elif scenario == "S5":
        router.update(wan_mode="dhcp", config_ok=0, uptime_s=2 * 3600)
    elif scenario == "S8":
        line.update(snr_db=9.5, errors_15min=45, last_drop_at=(now - timedelta(minutes=40)).isoformat(), drops_24h=4)


def seed(conn) -> None:
    rng = random.Random(SEED)
    now = clock.now()
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO sys_areas VALUES (?, ?, ?)", AREAS)
    conn.executemany("INSERT INTO sys_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)", PLANS)
    plan_wan = {p[0]: p[7] for p in PLANS}
    plan_price = {p[0]: p[5] for p in PLANS}
    quiet_areas = [a[0] for a in AREAS if a[0] not in OUTAGE_AREAS]

    customers = [dict(account_no=c.account_no, first_name=c.first_name, last_name=c.last_name, pin=c.pin, dob=c.dob,
                      language=c.language, area_code=c.area_code, plan_id=c.plan_id, router_model=c.router_model,
                      scenario=c.scenario) for c in DEMO_CUSTOMERS]
    for n in range(len(DEMO_CUSTOMERS) + 1, CUSTOMER_COUNT + 1):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice([name for name in LAST_NAMES if (name[0] < "А") == (first[0] < "А")])  # same alphabet
        customers.append(dict(
            account_no=f"QN-{100000 + n}", first_name=first, last_name=last, pin=f"{rng.randint(0, 9999):04d}",
            dob=f"{rng.randint(1950, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            language="bg" if first[0] >= "А" else "en", area_code=rng.choice(quiet_areas),
            plan_id=rng.choice([p[0] for p in PLANS]), router_model=rng.choice(list(ROUTERS)), scenario="healthy"))

    for c in customers:
        salt = f"{rng.getrandbits(64):016x}"
        status = "suspended" if c["scenario"] == "S6" else "active"
        conn.execute(
            "INSERT INTO sys_customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (c["account_no"], c["first_name"], c["last_name"], c["dob"], hash_pin(c["pin"], salt),
             f"+359 88 {rng.randint(100, 999)} {rng.randint(1000, 9999)}",
             f"{c['account_no'].lower()}@example.com", c["language"], f"{rng.randint(1, 120)} Demo Street",
             c["area_code"], c["plan_id"], c["router_model"], f"QNX{rng.getrandbits(32):08X}", status,
             f"{rng.randint(2015, 2025)}-{rng.randint(1, 12):02d}-01", c["scenario"]))
        line = _healthy_line(rng)
        router = _healthy_router(rng, c["router_model"], plan_wan[c["plan_id"]])
        wifi = {"2.4": dict(channel=router["wifi_band_2g_channel"], neighbour_networks=rng.randint(2, 8),
                            interference="low"),
                "5": dict(channel=router["wifi_band_5g_channel"], neighbour_networks=rng.randint(0, 3),
                          interference="low")}
        _apply_scenario(c["scenario"], line, router, wifi, now)
        router["wifi_band_2g_channel"] = wifi["2.4"]["channel"]
        conn.execute("INSERT INTO sys_line_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (c["account_no"], *line.values()))
        conn.execute("INSERT INTO sys_router_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (c["account_no"], *router.values()))
        for band, env in wifi.items():
            conn.execute("INSERT INTO sys_wifi_env VALUES (?, ?, ?, ?, ?)", (c["account_no"], band, *env.values()))
        _seed_bills(conn, c, plan_price[c["plan_id"]], now)

    _seed_outages(conn, now)
    _seed_slots(conn, now)


def _seed_bills(conn, customer, price, now):
    for months_ago in (2, 1, 0):
        period = (now.replace(day=1) - timedelta(days=months_ago * 30)).strftime("%Y-%m")
        due = f"{period}-20"
        bill_id = f"B-{customer['account_no'][3:]}-{period}"
        if customer["scenario"] == "S6" and months_ago == 1:
            row = (bill_id, customer["account_no"], period, price, due, None, "overdue")
        elif months_ago == 0 and due >= now.date().isoformat():
            row = (bill_id, customer["account_no"], period, price, due, None, "due")
        else:
            row = (bill_id, customer["account_no"], period, price, due, f"{period}-15", "paid")
        conn.execute("INSERT INTO sys_bills VALUES (?, ?, ?, ?, ?, ?, ?)", row)


def _seed_outages(conn, now):
    conn.execute("INSERT INTO sys_outages VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("OUT-2041", "SOF-02", "internet", (now - timedelta(hours=3)).isoformat(),
                  (now + timedelta(hours=4)).isoformat(), "active",
                  "A fibre cable was damaged during road works. Repair crews are on site."))
    conn.execute("INSERT INTO sys_outages VALUES (?, ?, ?, ?, ?, ?, ?)",
                 ("OUT-2042", "PLD-03", "tv", (now - timedelta(hours=1)).isoformat(),
                  (now + timedelta(hours=2)).isoformat(), "active",
                  "The TV platform has a technical fault. Internet is not affected."))


def _seed_slots(conn, now):
    first_day = now.date() + timedelta(days=1)
    rows = []
    for area_code, _, _ in AREAS:
        for day in range(14):
            date = first_day + timedelta(days=day)
            if date.weekday() == 6:  # no visits on Sundays
                continue
            for hour in (8, 10, 12, 14, 16):
                rows.append((f"SL-{area_code}-{date:%m%d}-{hour:02d}", area_code, f"{date}T{hour:02d}:00",
                             f"{date}T{hour + 2:02d}:00", 2, 0))
    conn.executemany("INSERT INTO sys_tech_slots VALUES (?, ?, ?, ?, ?, ?)", rows)


if __name__ == "__main__":
    from linecare import db

    db.ensure_database()
    print("Mock systems seeded in", db.config.DB_PATH)
