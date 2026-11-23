"""handover_to_human: always allowed, also unverified or locked. The summary is built in code (linecare/handover.py)."""
import json

from linecare import db
from linecare.crm import get_crm
from linecare.handover import build_summary
from linecare.policy.escalation import HANDOVER_REASONS, REPEAT_WINDOW_DAYS, handover_priority, is_repeat_contact
from linecare.session import HANDED_OVER
from linecare.tools.common import error, ok, set_outcome
from mocks import clock

SENTIMENTS = ("calm", "neutral", "frustrated", "angry")


def handover_to_human(session, reason: str, customer_view: str = "", suggested_next_step: str = "",
                      sentiment: str = "neutral") -> dict:
    if reason not in HANDOVER_REASONS:
        return error(f"reason must be one of {list(HANDOVER_REASONS)}")
    if sentiment not in SENTIMENTS:
        sentiment = "neutral"
    repeat = False
    if session.verified:
        try:
            repeat = is_repeat_contact(get_crm().list_cases(session.account_no, REPEAT_WINDOW_DAYS),
                                       session.case_category)
        except Exception:
            repeat = False  # the CRM history only raises the priority; the handover must not fail on it
    priority = handover_priority(reason, repeat_contact=repeat, sentiment=sentiment)

    with db.connect() as conn:
        number = conn.execute("SELECT COUNT(*) FROM handovers").fetchone()[0] + 1
        handover_id = f"HO-{number:04d}"
        summary = build_summary(session, handover_id=handover_id, reason=reason, priority=priority,
                                customer_view=customer_view, suggested_next_step=suggested_next_step,
                                sentiment=sentiment).model_dump()
        conn.execute("INSERT INTO handovers (handover_id, conv_id, account_no, created_at, priority, reason, "
                     "summary_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (handover_id, session.conv_id, session.account_no, clock.now().isoformat(), priority, reason,
                      json.dumps(summary, ensure_ascii=False)))
        queue_position = conn.execute("SELECT COUNT(*) FROM handovers WHERE status = 'queued'").fetchone()[0]
    if session.open_case_ref:
        try:
            get_crm().link_handover(session.open_case_ref, handover_id)
        except Exception:
            session.pending_crm_sync = True
    session.state, session.handover_id = HANDED_OVER, handover_id
    set_outcome(session, "handover")
    return ok(handover_id=handover_id, queue_position=queue_position, priority=priority,
              message="A colleague will take over with a summary of this conversation, so the customer does not "
                      "have to repeat anything. Tell them that and their place in the queue. Take no further "
                      "actions in this conversation.")


def get_handover(handover_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM handovers WHERE handover_id = ?", (handover_id,)).fetchone()
    return {**dict(row), "summary": json.loads(row["summary_json"])} if row else None
