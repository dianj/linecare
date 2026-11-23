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


def ensure_database() -> None:
    """Create the app tables and (re)seed the mock systems, so every start begins with fresh demo scenarios."""
    from mocks import seed

    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        seed.seed(conn)
        init_app_tables(conn)


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


def conversation_log(conv_id: str) -> dict:
    with connect() as conn:
        conversation = conn.execute("SELECT * FROM conversations WHERE conv_id = ?", (conv_id,)).fetchone()
        turns = [dict(r) for r in conn.execute("SELECT * FROM turns WHERE conv_id = ? ORDER BY turn_id", (conv_id,))]
        calls = [dict(r) for r in conn.execute(
            "SELECT tool_calls.* FROM tool_calls JOIN turns USING (turn_id) WHERE conv_id = ? ORDER BY id",
            (conv_id,))]
    return {"conversation": dict(conversation) if conversation else None, "turns": turns, "tool_calls": calls}
