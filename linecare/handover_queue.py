"""The operator queue: handovers from the customer assistant, claimed and closed by staff (ARCHITECTURE.md §7.4).

Also the retry of case writes that failed while the CRM was down: the handover summary keeps the case data
(`pending_crm_sync`), so nothing is lost and the case is created once the CRM answers again.
"""
import json
import logging
from datetime import datetime

from linecare import db
from linecare.crm import get_crm
from linecare.crm.base import CaseIn, Contact
from mocks import clock
from mocks import service as systems

log = logging.getLogger("linecare.queue")


class QueueError(Exception):
    """A queue action that is not possible now (unknown handover, already claimed, ...)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def list_handovers(status: str | None = None) -> list[dict]:
    query, params = "SELECT * FROM handovers", ()
    if status:
        query, params = query + " WHERE status = ?", (status,)
    with db.connect() as conn:
        rows = conn.execute(query + " ORDER BY CASE priority WHEN 'high' THEN 0 ELSE 1 END, created_at",
                            params).fetchall()
    now = clock.now()
    items = []
    for row in rows:
        summary = json.loads(row["summary_json"])
        items.append({"handover_id": row["handover_id"], "conv_id": row["conv_id"], "status": row["status"],
                      "priority": row["priority"], "reason": row["reason"], "created_at": row["created_at"],
                      "waiting_min": max(int((now - datetime.fromisoformat(row["created_at"])).total_seconds() // 60),
                                         0),
                      "claimed_by": row["claimed_by"], "account_no": row["account_no"],
                      "first_name": summary["customer"]["first_name"], "category": summary["issue"]["category"],
                      "customer_view": summary["issue"]["customer_view"], "open_case_ref": summary["open_case_ref"],
                      "language": summary["language"], "summary": summary})
    return items


def get_row(handover_id: str):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM handovers WHERE handover_id = ?", (handover_id,)).fetchone()
    if row is None:
        raise QueueError("not_found", f"No handover {handover_id}.")
    return row


def claim(handover_id: str, staff_id: str) -> None:
    get_row(handover_id)
    with db.connect() as conn:
        updated = conn.execute("UPDATE handovers SET status = 'claimed', claimed_by = ? WHERE handover_id = ? AND "
                               "status = 'queued'", (staff_id, handover_id)).rowcount
    if not updated:
        raise QueueError("not_queued", "This handover was already claimed or closed.")


def close(handover_id: str, staff, note: str) -> dict:
    """Close a claimed handover. The note goes to the case as a comment (INT-02: close only with a case comment)."""
    row = get_row(handover_id)
    if row["status"] != "claimed" or row["claimed_by"] != staff.staff_id:
        raise QueueError("not_yours", "Only the operator who claimed this handover can close it.")
    note = (note or "").strip()
    if len(note) < 5:
        raise QueueError("note_required", "Write what was done (it is added to the case).")
    case_ref = json.loads(row["summary_json"])["open_case_ref"]
    comment = None
    if case_ref:
        comment = get_crm().add_comment(case_ref, f"[{staff.role}] Handover {handover_id} closed: {note[:1500]}")
    with db.connect() as conn:
        conn.execute("UPDATE handovers SET status = 'closed', closed_at = ? WHERE handover_id = ?",
                     (clock.now().isoformat(), handover_id))
    db.audit(staff.staff_id, staff.role, "close_handover", row["account_no"], handover_id)
    return {"handover_id": handover_id, "status": "closed", "case_ref": case_ref, "comment_id": comment}


# --- assist results ------------------------------------------------------------------------------------------------

def assist_started(handover_id: str) -> None:
    with db.connect() as conn:
        conn.execute("INSERT INTO assists (handover_id, status, started_at) VALUES (?, 'pending', ?) "
                     "ON CONFLICT(handover_id) DO UPDATE SET status = 'pending', card_json = NULL, "
                     "started_at = excluded.started_at, finished_at = NULL", (handover_id, clock.now().isoformat()))


def assist_finished(handover_id: str, card: dict | None) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE assists SET status = ?, card_json = ?, finished_at = ? WHERE handover_id = ?",
                     ("ready" if card else "failed", json.dumps(card, ensure_ascii=False) if card else None,
                      clock.now().isoformat(), handover_id))


def get_assist(handover_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM assists WHERE handover_id = ?", (handover_id,)).fetchone()
    if row is None:
        return None
    return {"status": row["status"], "card": json.loads(row["card_json"]) if row["card_json"] else None}


# --- cases that could not be written while the CRM was down -------------------------------------------------------

def sync_pending_cases() -> int:
    """Create the cases of handovers marked `pending_crm_sync`; returns how many were written."""
    with db.connect() as conn:
        rows = conn.execute("SELECT handover_id, account_no, summary_json FROM handovers").fetchall()
    written = 0
    for row in rows:
        summary = json.loads(row["summary_json"])
        if not summary.get("pending_crm_sync") or not row["account_no"]:
            continue
        try:
            crm = get_crm()
            case_ref = summary["open_case_ref"]
            if not case_ref:
                customer = systems.get_customer(row["account_no"])
                crm.upsert_contact(Contact(row["account_no"], customer["first_name"], customer["last_name"]))
                checks = "\n".join(f"{c['tool']}: {c['result']}" for c in summary["checks"])
                case_ref = crm.create_case(CaseIn(
                    account_no=row["account_no"], category=summary["issue"]["category"]
                    if summary["issue"]["category"] != "unknown" else "other",
                    subject=f"Handover {row['handover_id']}: {summary['reason'].replace('_', ' ')}",
                    description=summary["issue"]["customer_view"], diagnostics=checks)).case_ref
            crm.link_handover(case_ref, row["handover_id"])
        except Exception:
            log.warning("CRM still unavailable for %s; will retry", row["handover_id"])
            continue
        summary.update(open_case_ref=case_ref, pending_crm_sync=False)
        with db.connect() as conn:
            conn.execute("UPDATE handovers SET summary_json = ? WHERE handover_id = ?",
                         (json.dumps(summary, ensure_ascii=False), row["handover_id"]))
        written += 1
        log.info("Case %s written for %s after a CRM outage", case_ref, row["handover_id"])
    return written
