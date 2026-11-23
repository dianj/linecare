"""When a technician may be booked (ARCHITECTURE.md §6.3). Decided in code from tool results, never by the model.

Allowed when the diagnostics in this conversation found (a) a line fault (`no_sync` or `degraded`), (b) a router
that stays offline (hardware), or (c) intermittent drops (3 or more in 24 hours). Never during an area outage,
for a suspended account, or before any diagnostics ran.
"""
from dataclasses import dataclass

MIN_DROPS_24H = 3

REFUSALS = {
    "account_suspended": "The account is suspended for an unpaid bill; a visit cannot restore the service. Explain "
                         "the overdue balance instead (get_bill_status).",
    "area_outage": "There is an active outage in the customer's area and the repair crew is already working on it. "
                   "Tell the customer the expected fix time; no visit is needed.",
    "diagnostics_required": "Run the remote checks first (run_line_test and get_router_status).",
    "no_fault_found": "The remote checks found no fault on Quillnet's side, so a visit is not justified. Continue "
                      "with the guided steps from the help articles, or offer a colleague.",
}


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str  # the basis when eligible, else the refusal code


def technician_eligibility(*, line: dict | None, router: dict | None, area_outage: bool,
                           account_status: str | None, override: bool = False) -> Eligibility:
    if account_status == "suspended":
        return Eligibility(False, "account_suspended")
    if area_outage:
        return Eligibility(False, "area_outage")
    if override:  # helpdesk / operator decision (D3)
        return Eligibility(True, "staff_override")
    if line is None and router is None:
        return Eligibility(False, "diagnostics_required")
    if line and line.get("verdict") in ("no_sync", "degraded"):
        return Eligibility(True, "line_fault")
    if router and router.get("verdict") == "offline":
        return Eligibility(True, "router_hardware")
    if line and (line.get("drops_24h") or 0) >= MIN_DROPS_24H:
        return Eligibility(True, "intermittent_drops")
    return Eligibility(False, "no_fault_found")
