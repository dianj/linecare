"""SQLite: one file for the mock systems (tables `sys_*`, see mocks/seed.py) and LineCare's own tables.

LineCare's tables hold the conversation log used for audits and, later, the metrics. Everything written here is
already redacted: no PIN or date of birth is ever stored (see linecare/redaction.py).
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager

from linecare import config
from mocks import clock

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conv_id TEXT PRIMARY KEY,
    agent TEXT NOT NULL CHECK (agent IN ('customer', 'helpdesk')),
    channel TEXT NOT NULL DEFAULT 'chat' CHECK (channel IN ('chat', 'voice')),
    model TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    language TEXT,
    account_no TEXT,
    outcome TEXT,
    scenario_tag TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL REFERENCES conversations(conv_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text_redacted TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    stt_ms INTEGER,
    llm_ms INTEGER,
    tools_ms INTEGER,
    tts_ms INTEGER,
    rounds INTEGER
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL REFERENCES turns(turn_id),
    tool TEXT NOT NULL,
    args_redacted_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_summary TEXT,
    ms INTEGER
);
CREATE TABLE IF NOT EXISTS handovers (
    handover_id TEXT PRIMARY KEY,
    conv_id TEXT NOT NULL REFERENCES conversations(conv_id),
    account_no TEXT,
    created_at TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('normal', 'high')),
    reason TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'claimed', 'closed')),
    claimed_by TEXT,
    closed_at TEXT
);
-- Staff access to customer data (helpdesk tools): who looked, in which role, and why (ARCHITECTURE.md §7.1).
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    account_no TEXT,
    reason TEXT
);
-- Tickets for the network, provisioning and billing teams (mock: they stay here).
CREATE TABLE IF NOT EXISTS internal_tickets (
    ticket_ref TEXT PRIMARY KEY,
    team TEXT NOT NULL CHECK (team IN ('network', 'provisioning', 'billing')),
    summary TEXT NOT NULL,
    case_ref TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
-- The operator assist for a claimed handover (ARCHITECTURE.md §7.4), written by a background job.
CREATE TABLE IF NOT EXISTS assists (
    handover_id TEXT PRIMARY KEY REFERENCES handovers(handover_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'failed')),
    card_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
-- Failed identity checks per account number, across conversations (ARCHITECTURE.md §6.1). Kept for any number
-- that was tried, also numbers that do not exist, so a lock does not reveal which accounts exist.
CREATE TABLE IF NOT EXISTS identity_locks (
    account_no TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);
-- FakeCRM: the fields LineCare uses of Salesforce Contact, Case and CaseComment (ARCHITECTURE.md §9).
CREATE TABLE IF NOT EXISTS crm_contacts (
    contact_id TEXT PRIMARY KEY,
    account_no TEXT NOT NULL UNIQUE,
    first_name TEXT,
    last_name TEXT
);
CREATE TABLE IF NOT EXISTS crm_cases (
    case_ref TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL REFERENCES crm_contacts(contact_id),
    account_no TEXT NOT NULL,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT,
    diagnostics TEXT,
    status TEXT NOT NULL DEFAULT 'New',
    origin TEXT NOT NULL DEFAULT 'Chat',
    priority TEXT NOT NULL DEFAULT 'Medium',
    handover_id TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS crm_case_comments (
    comment_id TEXT PRIMARY KEY,
    case_ref TEXT NOT NULL REFERENCES crm_cases(case_ref),
    body TEXT NOT NULL,
    is_public INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_app_tables(conn) -> None:
    conn.executescript(APP_SCHEMA)


# Scenario S9 (QN-100009, Martin Kolev): the same intermittent fault three times in 60 days, the last visit missed.
# Case numbers below 1000 mark history; new cases start at 00001001 (FakeCRM). The Salesforce backend has no
# seeded history: its CreatedDate cannot be set through the API.
CRM_HISTORY = [  # case_ref, days ago, subject
    ("00000951", 52, "Internet drops several times a day"),
    ("00000952", 24, "Internet drops again in the evening"),
    ("00000953", 9, "Drops again; technician visit booked"),
]


def seed_crm_history(conn) -> None:
    """The FakeCRM history of the demo customers (idempotent: it runs at every start)."""
    from datetime import timedelta

    now = clock.now()
    conn.execute("INSERT OR IGNORE INTO crm_contacts VALUES ('003HIST000009', 'QN-100009', 'Martin', 'Kolev')")
    conn.execute("DELETE FROM crm_case_comments WHERE case_ref < '00001000'")
    conn.execute("DELETE FROM crm_cases WHERE case_ref < '00001000'")
    for case_ref, days_ago, subject in CRM_HISTORY:
        created = (now - timedelta(days=days_ago)).replace(microsecond=0).isoformat()
        conn.execute("INSERT INTO crm_cases (case_ref, contact_id, account_no, category, subject, description, "
                     "diagnostics, status, origin, priority, created_at, closed_at) VALUES "
                     "(?, '003HIST000009', 'QN-100009', 'intermittent', ?, 'The connection drops for a few minutes.', "
                     "'run_line_test: ok, drops in the last 24 h', 'Closed', 'Phone', 'Medium', ?, ?)",
                     (case_ref, subject, created, created))
    conn.execute("INSERT INTO crm_case_comments VALUES ('00aHIST000953', '00000953', 'Technician did not arrive "
                 "(dispatch error on our side). Customer informed.', 0, ?)",
                 ((now - timedelta(days=9)).replace(hour=12, minute=0, microsecond=0).isoformat(),))


def ensure_database() -> None:
    """Create the app tables and (re)seed the mock systems, so every start begins with fresh demo scenarios."""
    from mocks import seed

    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        seed.seed(conn)
        init_app_tables(conn)
        seed_crm_history(conn)


# --- identity locks per account number ---------------------------------------------------

def account_locked_until(account_no: str):
    """The end of an active lock on this account number, or None."""
    from datetime import datetime

    with connect() as conn:
        row = conn.execute("SELECT locked_until FROM identity_locks WHERE account_no = ?", (account_no,)).fetchone()
    if row is None or row["locked_until"] is None:
        return None
    until = datetime.fromisoformat(row["locked_until"])
    return until if until > clock.now() else None


def record_account_failure(account_no: str) -> bool:
    """Count a failed check for this account number; returns True when it locks the account."""
    from datetime import timedelta

    with connect() as conn:
        row = conn.execute("SELECT failures, locked_until FROM identity_locks WHERE account_no = ?",
                           (account_no,)).fetchone()
        failures = 1 if row is None or row["locked_until"] else row["failures"] + 1  # a past lock starts afresh
        locked_until = None
        if failures >= config.MAX_IDENTITY_ATTEMPTS:
            locked_until = (clock.now() + timedelta(minutes=config.IDENTITY_LOCK_MINUTES)).isoformat()
        conn.execute("INSERT INTO identity_locks VALUES (?, ?, ?) ON CONFLICT(account_no) DO UPDATE SET "
                     "failures = excluded.failures, locked_until = excluded.locked_until",
                     (account_no, failures, locked_until))
    return locked_until is not None


def clear_account_failures(account_no: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM identity_locks WHERE account_no = ?", (account_no,))


# --- conversation log ---------------------------------------------------------------------

def new_conversation(agent: str, model: str, channel: str = "chat") -> str:
    conv_id = f"CV-{uuid.uuid4().hex[:10]}"
    with connect() as conn:
        conn.execute("INSERT INTO conversations (conv_id, agent, channel, model, started_at) VALUES (?, ?, ?, ?, ?)",
                     (conv_id, agent, channel, model, clock.now().isoformat()))
    return conv_id


def update_conversation(conv_id: str, **fields) -> None:
    allowed = {"channel", "model", "ended_at", "language", "account_no", "outcome", "scenario_tag"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with connect() as conn:
            conn.execute(f"UPDATE conversations SET {assignments} WHERE conv_id = ?", (*fields.values(), conv_id))


def log_turn(conv_id: str, role: str, text_redacted: str, model: str = None, *, llm_ms: int = None,
             tools_ms: int = None, stt_ms: int = None, rounds: int = None, tool_calls: list = ()) -> int:
    """Store one turn and its tool calls. Callers pass redacted text and redacted arguments only."""
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO turns (conv_id, role, text_redacted, model, created_at, stt_ms, llm_ms, tools_ms, rounds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, role, text_redacted, model, clock.now().isoformat(), stt_ms, llm_ms, tools_ms, rounds))
        turn_id = cursor.lastrowid
        for call in tool_calls:
            conn.execute(
                "INSERT INTO tool_calls (turn_id, tool, args_redacted_json, status, result_summary, ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (turn_id, call["name"], json.dumps(call["arguments"], ensure_ascii=False), call["status"],
                 call.get("summary"), call.get("ms")))
    return turn_id


def audit(staff_id: str, role: str, action: str, account_no: str = None, reason: str = None) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit_log (at, staff_id, role, action, account_no, reason) VALUES (?, ?, ?, ?, ?, ?)",
                     (clock.now().isoformat(), staff_id, role, action, account_no, reason))


def audit_entries(account_no: str = None) -> list[dict]:
    query, params = "SELECT * FROM audit_log", ()
    if account_no:
        query, params = query + " WHERE account_no = ?", (account_no,)
    with connect() as conn:
        return [dict(r) for r in conn.execute(query + " ORDER BY id", params)]


def conversation_log(conv_id: str) -> dict:
    with connect() as conn:
        conversation = conn.execute("SELECT * FROM conversations WHERE conv_id = ?", (conv_id,)).fetchone()
        turns = [dict(r) for r in conn.execute("SELECT * FROM turns WHERE conv_id = ? ORDER BY turn_id", (conv_id,))]
        calls = [dict(r) for r in conn.execute(
            "SELECT tool_calls.* FROM tool_calls JOIN turns USING (turn_id) WHERE conv_id = ? ORDER BY id",
            (conv_id,))]
    return {"conversation": dict(conversation) if conversation else None, "turns": turns, "tool_calls": calls}
