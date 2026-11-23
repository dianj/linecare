"""The dispatcher for the model's tool calls: allow-list per agent, identity gate, argument check, run.

`execute_tool_call` never raises; it always returns a result dict with a `status`. Arguments the tool does not
declare are dropped, so the model cannot widen a tool's reach by inventing parameters.
"""
import inspect
import json
import logging

from linecare.session import HANDED_OVER, LOCKED, VERIFIED
from linecare.tools.billing import get_bill_status
from linecare.tools.cases import create_case
from linecare.tools.common import blocked, error
from linecare.tools.diagnostics import (
    change_wifi_channel,
    get_router_status,
    reboot_router,
    run_line_test,
    scan_wifi,
)
from linecare.tools.handover import handover_to_human
from linecare.tools.helpdesk import (
    add_case_comment,
    calculate_compensation,
    create_internal_ticket,
    get_case,
    get_customer_overview,
    get_handover,
    search_kb,
)
from linecare.tools.identity import verify_customer
from linecare.tools.kb import search_help
from linecare.tools.network import check_outage
from linecare.tools.outcome import record_outcome
from linecare.tools.scheduling import book_technician

log = logging.getLogger("linecare.registry")

CUSTOMER_FUNCTIONS = {
    "verify_customer": verify_customer,
    "check_outage": check_outage,
    "run_line_test": run_line_test,
    "get_router_status": get_router_status,
    "reboot_router": reboot_router,
    "scan_wifi": scan_wifi,
    "change_wifi_channel": change_wifi_channel,
    "get_bill_status": get_bill_status,
    "search_help": search_help,
    "create_case": create_case,
    "book_technician": book_technician,
    "handover_to_human": handover_to_human,
    "record_outcome": record_outcome,
}
HELPDESK_FUNCTIONS = {
    "search_kb": search_kb,
    "get_customer_overview": get_customer_overview,
    "get_case": get_case,
    "add_case_comment": add_case_comment,
    "calculate_compensation": calculate_compensation,
    "create_internal_ticket": create_internal_ticket,
    "get_handover": get_handover,
}
# Staff roles: technicians do not decide credits.
ALLOWED = {
    "customer": CUSTOMER_FUNCTIONS,
    "operator": HELPDESK_FUNCTIONS,
    "technician": {k: v for k, v in HELPDESK_FUNCTIONS.items() if k != "calculate_compensation"},
}

# Tools an unverified caller may use; everything else needs a verified identity.
PUBLIC_TOOLS = {"verify_customer", "check_outage", "search_help", "handover_to_human"}
# While identity checks are locked, only public help and a human remain.
LOCKED_TOOLS = {"search_help", "handover_to_human"}


def _gate(session, name: str) -> dict | None:
    """The session rule for this tool, or None when the call may go ahead."""
    if session.state == HANDED_OVER:
        return blocked("handed_over", "The conversation was handed to a colleague; do not take further actions.")
    if session.state == LOCKED and name not in LOCKED_TOOLS:
        return blocked("identity_locked", "Identity checks are locked. Offer to connect the customer with a "
                       "colleague.", suggest_handover=True)
    if session.state != VERIFIED and name not in PUBLIC_TOOLS:
        return blocked("identity_required", "Verify the customer first with verify_customer (account number and "
                       "PIN or date of birth).")
    return None


def execute_tool_call(agent: str, session, name: str, raw_arguments: str) -> dict:
    """`agent`: "customer", or a staff role ("operator", "technician") for the helpdesk agent."""
    functions = ALLOWED.get(agent, {})
    if name not in functions:
        return error(f"Unknown tool '{name}'. Available: {sorted(functions)}")
    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError as e:
        return error(f"Tool arguments are not valid JSON: {e}")
    if not isinstance(arguments, dict):
        return error("Tool arguments must be a JSON object.")
    refusal = _gate(session, name) if agent == "customer" else None  # staff have no identity gate
    if refusal:
        return refusal

    function = functions[name]
    parameters = list(inspect.signature(function).parameters)[1:]  # the first one is the session
    arguments = {k: v for k, v in arguments.items() if k in parameters}
    try:
        inspect.signature(function).bind(session, **arguments)
    except TypeError as e:
        return error(f"Invalid arguments for '{name}': {e}")
    try:
        return function(session, **arguments)
    except Exception:
        # Details stay in the server log; the model only learns that the system failed.
        log.exception("Tool %s failed", name)
        return {"status": "system_error", "message": "The support systems are temporarily unavailable. Apologise "
                "and offer to try again later."}
