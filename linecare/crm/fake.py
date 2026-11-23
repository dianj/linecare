"""FakeCRM: the CRM interface on SQLite tables `crm_*`, with Salesforce-like case numbers (00001001, ...)."""
from datetime import timedelta

from linecare.crm.base import OPEN_STATUSES, CaseDetail, CaseIn, CaseRef, CaseSummary, Contact
from linecare.db import connect
from mocks import clock

FIRST_CASE_NUMBER = 1001


class FakeCRM:
    def find_contact(self, account_no):
        with connect() as conn:
            row = conn.execute("SELECT * FROM crm_contacts WHERE account_no = ?", (account_no,)).fetchone()
        return Contact(row["account_no"], row["first_name"], row["last_name"], row["contact_id"]) if row else None

    def upsert_contact(self, contact):
        existing = self.find_contact(contact.account_no)
        with connect() as conn:
            if existing:
                conn.execute("UPDATE crm_contacts SET first_name = ?, last_name = ? WHERE contact_id = ?",
                             (contact.first_name, contact.last_name, existing.contact_id))
                return existing.contact_id
            count = conn.execute("SELECT COUNT(*) FROM crm_contacts WHERE contact_id LIKE '003FAKE%'").fetchone()[0]
            contact_id = f"003FAKE{count + 1:06d}"
            conn.execute("INSERT INTO crm_contacts VALUES (?, ?, ?, ?)",
                         (contact_id, contact.account_no, contact.first_name, contact.last_name))
        return contact_id

    def get_open_case(self, account_no, category):
        with connect() as conn:
            row = conn.execute(
                f"SELECT case_ref, status FROM crm_cases WHERE account_no = ? AND category = ? AND status IN "
                f"({','.join('?' * len(OPEN_STATUSES))}) ORDER BY created_at DESC LIMIT 1",
                (account_no, category, *OPEN_STATUSES)).fetchone()
        return CaseRef(row["case_ref"], row["status"], created=False) if row else None

    def create_case(self, case: CaseIn):
        """Idempotent per account + category while a case is open: returns the open case instead of a new one."""
        existing = self.get_open_case(case.account_no, case.category)
        if existing:
            return existing
        contact = self.find_contact(case.account_no)
        if contact is None:
            raise LookupError(f"no CRM contact for {case.account_no}")
        with connect() as conn:
            # History (case numbers below 1000, see db.seed_crm_history) does not shift new numbers.
            last = conn.execute("SELECT MAX(CAST(case_ref AS INTEGER)) FROM crm_cases").fetchone()[0] or 0
            number = max(last + 1, FIRST_CASE_NUMBER)
            case_ref = f"{number:08d}"
            conn.execute(
                "INSERT INTO crm_cases (case_ref, contact_id, account_no, category, subject, description, "
                "diagnostics, origin, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_ref, contact.contact_id, case.account_no, case.category, case.subject, case.description,
                 case.diagnostics, case.origin, case.priority, clock.now().isoformat()))
        return CaseRef(case_ref, "New", created=True)

    def list_cases(self, account_no, since_days):
        since = (clock.now() - timedelta(days=since_days)).isoformat()
        with connect() as conn:
            rows = conn.execute("SELECT * FROM crm_cases WHERE account_no = ? AND created_at >= ? "
                                "ORDER BY created_at DESC", (account_no, since)).fetchall()
        return [CaseSummary(r["case_ref"], r["category"], r["subject"], r["status"], r["created_at"]) for r in rows]

    def get_case(self, case_ref):
        with connect() as conn:
            row = conn.execute("SELECT * FROM crm_cases WHERE case_ref = ?", (case_ref,)).fetchone()
            comments = conn.execute("SELECT body, is_public, created_at FROM crm_case_comments WHERE case_ref = ? "
                                    "ORDER BY created_at, comment_id", (case_ref,)).fetchall()
        if row is None:
            return None
        return CaseDetail(row["case_ref"], row["category"], row["subject"], row["status"], row["created_at"],
                          account_no=row["account_no"], description=row["description"],
                          diagnostics=row["diagnostics"], priority=row["priority"],
                          comments=[{"body": c["body"], "is_public": bool(c["is_public"]),
                                     "created_at": c["created_at"]} for c in comments])

    def add_comment(self, case_ref, text, is_public=False):
        with connect() as conn:
            if conn.execute("SELECT 1 FROM crm_cases WHERE case_ref = ?", (case_ref,)).fetchone() is None:
                raise LookupError(f"unknown case {case_ref}")
            count = conn.execute("SELECT COUNT(*) FROM crm_case_comments WHERE comment_id LIKE '00aFAKE%'")\
                .fetchone()[0]
            comment_id = f"00aFAKE{count + 1:06d}"
            conn.execute("INSERT INTO crm_case_comments VALUES (?, ?, ?, ?, ?)",
                         (comment_id, case_ref, text, int(is_public), clock.now().isoformat()))
        return comment_id

    def link_handover(self, case_ref, handover_id):
        with connect() as conn:
            conn.execute("UPDATE crm_cases SET handover_id = ?, status = 'Escalated' WHERE case_ref = ?",
                         (handover_id, case_ref))
