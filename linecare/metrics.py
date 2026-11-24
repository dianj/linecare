"""KPIs from the conversation log (ARCHITECTURE.md §12.3). Read-only queries over the tables code already writes.

The base for the customer KPIs is every customer conversation with at least one user turn in the filter (date range
on the start time, model). Outcomes are set by code from tool results, never from the model's wording.
"""
import math
import re
from collections import Counter, defaultdict

from linecare import db

CONTAINED = ("resolved", "info")
FIXES = ("reboot_router", "change_wifi_channel")
FAILED = ("error", "system_error")
REASON = re.compile(r"reason=([a-z_]+)")
RECENT = 20


def percentile(values: list[int], p: int) -> int | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)) - 1, 0)]


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _conversations(conn, date_from: str | None, date_to: str | None, model: str | None) -> list[dict]:
    query = ("SELECT c.*, (SELECT COUNT(*) FROM turns t WHERE t.conv_id = c.conv_id AND t.role = 'user') AS user_turns "
             "FROM conversations c WHERE c.agent = 'customer'")
    params: list = []
    if date_from:
        query, params = query + " AND substr(c.started_at, 1, 10) >= ?", [*params, date_from]
    if date_to:
        query, params = query + " AND substr(c.started_at, 1, 10) <= ?", [*params, date_to]
    if model:
        query, params = query + " AND c.model = ?", [*params, model]
    rows = [dict(r) for r in conn.execute(query + " ORDER BY c.started_at DESC, c.conv_id", params)]
    return [r for r in rows if r["user_turns"] > 0]


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def compute(date_from: str = None, date_to: str = None, model: str = None) -> dict:
    with db.connect() as conn:
        conversations = _conversations(conn, date_from, date_to, model)
        ids = [c["conv_id"] for c in conversations]
        turns = [dict(r) for r in conn.execute(
            f"SELECT * FROM turns WHERE conv_id IN ({_in(ids)}) ORDER BY turn_id", ids)] if ids else []
        calls = [dict(r) for r in conn.execute(
            f"SELECT tool_calls.*, turns.conv_id FROM tool_calls JOIN turns USING (turn_id) "
            f"WHERE turns.conv_id IN ({_in(ids)}) ORDER BY tool_calls.id", ids)] if ids else []
        reasons = dict(conn.execute(
            f"SELECT conv_id, reason FROM handovers WHERE conv_id IN ({_in(ids)})", ids).fetchall()) if ids else {}
        models = [r[0] for r in conn.execute(
            "SELECT DISTINCT model FROM conversations WHERE agent = 'customer' AND model IS NOT NULL ORDER BY model")]

    base = len(conversations)
    outcomes = Counter(c["outcome"] or "none" for c in conversations)
    handed_over = [c for c in conversations if c["outcome"] == "handover"]

    by_scenario: dict[str, Counter] = defaultdict(Counter)
    for c in conversations:
        by_scenario[c["scenario_tag"] or "other"][c["outcome"] or "none"] += 1

    resolved_turns = [c["user_turns"] for c in conversations if c["outcome"] == "resolved"]

    return {
        "filter": {"from": date_from, "to": date_to, "model": model},
        "models": models,
        "conversations": base,
        "containment_rate": rate(sum(outcomes[o] for o in CONTAINED), base),
        "escalation_rate": rate(len(handed_over), base),
        "escalation_reasons": dict(Counter(reasons.get(c["conv_id"], "unknown") for c in handed_over).most_common()),
        "outcomes": dict(outcomes.most_common()),
        "by_scenario": {tag: dict(counts) for tag, counts in sorted(by_scenario.items(), key=_scenario_order)},
        "avg_turns_to_resolution": round(sum(resolved_turns) / len(resolved_turns), 1) if resolved_turns else None,
        "latency_ms": _latency(turns),
        "tool_calls": len(calls),
        "tool_error_rate": rate(sum(c["status"] in FAILED for c in calls), len(calls)),
        "blocked_actions": dict(Counter(_reason(c) for c in calls if c["status"] == "blocked").most_common()),
        "fixed_remotely": _fixed_remotely(calls),
        "recent": [{k: c[k] for k in ("conv_id", "started_at", "model", "channel", "language", "scenario_tag",
                                      "outcome", "user_turns")} for c in conversations[:RECENT]],
    }


def _scenario_order(item) -> tuple:
    tag = item[0]
    return (0, int(tag[1:])) if re.fullmatch(r"S\d+", tag) else (1, 0)


def _reason(call: dict) -> str:
    found = REASON.search(call.get("result_summary") or "")
    return found.group(1) if found else "unknown"


def _latency(turns: list[dict]) -> dict:
    """p50/p95 per stage over the assistant turns; end to end = speech-to-text of the user's turn + LLM + tools."""
    stages: dict[str, list[int]] = defaultdict(list)
    last_user: dict[str, dict] = {}
    for turn in turns:
        if turn["role"] == "user":
            last_user[turn["conv_id"]] = turn
            if turn["stt_ms"] is not None:
                stages["stt"].append(turn["stt_ms"])
            continue
        if turn["llm_ms"] is None:
            continue
        stages["llm"].append(turn["llm_ms"])
        stages["tools"].append(turn["tools_ms"] or 0)
        stt = (last_user.get(turn["conv_id"]) or {}).get("stt_ms") or 0
        stages["end_to_end"].append(stt + turn["llm_ms"] + (turn["tools_ms"] or 0))
    return {stage: {"p50": percentile(values, 50), "p95": percentile(values, 95), "n": len(values)}
            for stage, values in stages.items()}


def _fixed_remotely(calls: list[dict]) -> int:
    """Conversations where a remote fix ran (restart, channel change) and no technician was booked."""
    fixed = {c["conv_id"] for c in calls if c["tool"] in FIXES and c["status"] == "ok"}
    booked = {c["conv_id"] for c in calls if c["tool"] == "book_technician" and c["status"] == "ok"}
    return len(fixed - booked)
