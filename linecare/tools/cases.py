"""create_case: one open CRM case per issue per account; the diagnostics come from the tool log, not the model."""
import logging

from linecare.crm import get_crm
from linecare.crm.base import CASE_CATEGORIES, CaseIn, Contact
from linecare.policy.escalation import REPEAT_WINDOW_DAYS, is_repeat_contact
from linecare.redaction import redact_text
from linecare.tools.common import blocked, error, ok, set_outcome
from mocks import service as systems

log = logging.getLogger("linecare.tools")

# Categories an area outage already explains, and the outage service that explains them.
OUTAGE_SERVICE = {"internet_down": "internet", "slow_internet": "internet", "intermittent": "internet", "tv": "tv"}


def create_case(session, category: str, subject: str, description: str = "", diagnostics_summary: str = "") -> dict:
    if category not in CASE_CATEGORIES:
        return error(f"category must be one of {list(CASE_CATEGORIES)}")
    subject = str(subject or "").strip()
    if not subject:
        return error("Give a short subject, e.g. 'No internet, line test degraded'.")
    service = OUTAGE_SERVICE.get(category)
    if service and systems.outages([session.area_code], service):
        return blocked("known_outage", "This problem is explained by a known outage in the customer's area, which "
                       "is already being repaired; no case is needed. Tell the customer the expected fix time.")

    def clean(text):
        return redact_text(str(text or ""), session.secrets, identity_phase=True)[:2000]

    checks = [f"{e['tool']}: {e['result']}" for e in session.tool_log]
    diagnostics = "\n".join(checks + ([f"agent notes: {clean(diagnostics_summary)}"] if diagnostics_summary else []))
    crm = get_crm()
    try:
        # Escalation policy (S9): earlier cases for the same issue, counted before this one is written.
        repeat = is_repeat_contact(crm.list_cases(session.account_no, REPEAT_WINDOW_DAYS), category)
        customer = systems.get_customer(session.account_no)
        crm.upsert_contact(Contact(session.account_no, customer["first_name"], customer["last_name"]))
        ref = crm.create_case(CaseIn(account_no=session.account_no, category=category, subject=clean(subject)[:200],
                                     description=clean(description), diagnostics=diagnostics))
    except Exception:
        log.exception("CRM create_case failed")
        session.pending_crm_sync = True
        return error("The case system is not reachable right now. Apologise and offer to connect the customer "
                     "with a colleague; the details will be passed on.", reason="crm_unavailable")
    session.open_case_ref, session.case_category = ref.case_ref, category
    set_outcome(session, "case_created")
    if repeat:
        session.repeat_contact = True
        return ok(case_ref=ref.case_ref, already_open=not ref.created, suggest_handover=True,
                  next_step="This customer has contacted us about the same problem several times in the last "
                            f"{REPEAT_WINDOW_DAYS} days. Give the case number, acknowledge the repeated trouble and "
                            "offer a colleague now: handover_to_human with reason repeat_contact.")
    if not ref.created:
        return ok(case_ref=ref.case_ref, already_open=True,
                  message="An open case for this issue already exists; it was not duplicated. Give the customer "
                          "this case number.")
    return ok(case_ref=ref.case_ref, already_open=False,
              message="Case created. Give the customer the case number, read in groups (e.g. 0000 1001).")
