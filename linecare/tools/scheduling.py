"""book_technician: eligibility in code (policy/technician.py), an open case required, two steps."""
import logging
from datetime import datetime

from linecare.crm import get_crm
from linecare.policy.technician import REFUSALS, technician_eligibility
from linecare.tools.common import blocked, error, ok, set_outcome, two_step
from mocks import clock
from mocks import service as systems

log = logging.getLogger("linecare.tools")


def slot_label(slot: dict) -> str:
    start, end = datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"])
    return f"{start:%A %Y-%m-%d}, {start:%H:%M}-{end:%H:%M}"


def book_technician(session, slot_id: str = None, confirmation_id: str = None) -> dict:
    if session.appointment:
        return blocked("already_booked", f"A visit is already booked for {session.appointment['label']}.")
    eligibility = technician_eligibility(
        line=session.diagnostics.get("run_line_test"), router=session.diagnostics.get("get_router_status"),
        area_outage=bool(systems.outages([session.area_code], "internet")), account_status=session.account_status)
    if not eligibility.eligible:
        return blocked(eligibility.reason, REFUSALS[eligibility.reason])
    if not session.open_case_ref:
        return blocked("case_required", "Every visit needs a case. Call create_case first (category, subject and "
                       "a short summary of the checks), then call book_technician again.")

    def execute(arguments):
        booking = systems.book_appointment(session.account_no, arguments["slot_id"], eligibility.reason,
                                           session.open_case_ref)
        if booking is None:
            return blocked("slot_taken", "That window was just taken. Call book_technician without arguments to "
                           "get the current free windows.")
        label = slot_label(booking["slot"])
        session.appointment = {"appointment_id": booking["appointment_id"], "slot_id": arguments["slot_id"],
                               "label": label}
        set_outcome(session, "technician_booked")
        try:
            get_crm().add_comment(session.open_case_ref, f"Technician visit booked: {label} "
                                  f"({booking['appointment_id']}, basis: {eligibility.reason}).")
        except Exception:
            log.exception("CRM comment failed")
            session.pending_crm_sync = True
        return ok(appointment_id=booking["appointment_id"], slot={"slot_id": arguments["slot_id"], "label": label},
                  message="The visit is booked. Tell the customer the day and window, and that an adult must be "
                          "at home.")

    if confirmation_id:
        return two_step(session, "book_technician", {}, "", confirmation_id, execute)
    if not slot_id:
        slots = systems.free_slots(session.area_code)
        if not slots:
            return blocked("no_slots", "There are no free windows in the next two weeks. Offer a colleague.")
        return ok(slots=[{"slot_id": s["slot_id"], "label": slot_label(s)} for s in slots],
                  message="Offer these windows (day and time only, not the slot ids). When the customer picks one, "
                          "call book_technician with its slot_id.")
    slot = systems.get_slot(slot_id)
    if (slot is None or slot["area_code"] != session.area_code or slot["booked"] >= slot["capacity"]
            or slot["start"] <= clock.now().isoformat()):
        return error("Unknown or unavailable slot_id. Call book_technician without arguments to get the free "
                     "windows.")
    label = slot_label(slot)
    summary = f"Book a technician visit on {label} for case {session.open_case_ref}."
    return two_step(session, "book_technician", {"slot_id": slot["slot_id"]}, summary, None, execute)
