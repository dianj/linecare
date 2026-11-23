"""The handover summary a colleague sees (ARCHITECTURE.md §11).

Built in code from the session and the tool log, not written by the model: the model only adds one sentence of
the customer's view and a suggested next step, and both are redacted. Validated with Pydantic.
"""
import json
from typing import Literal

from pydantic import BaseModel, Field

from linecare.redaction import redact_text
from mocks import clock

DIAGNOSTIC_TOOLS = ("check_outage", "run_line_test", "get_router_status", "scan_wifi", "get_bill_status")
ACTION_TOOLS = ("reboot_router", "create_case", "book_technician", "record_outcome")


class Customer(BaseModel):
    account_no: str | None
    first_name: str | None
    plan: str | None
    verified: bool


class Issue(BaseModel):
    category: str
    customer_view: str = Field(max_length=400)


class Step(BaseModel):
    tool: str
    result: str


class HandoverSummary(BaseModel):
    handover_id: str
    created_at: str
    priority: Literal["normal", "high"]
    reason: str
    language: str
    customer: Customer
    issue: Issue
    checks: list[Step]
    actions: list[Step]
    open_case_ref: str | None
    pending_crm_sync: bool = False
    kb_sources: list[str]
    sentiment: Literal["calm", "neutral", "frustrated", "angry"]
    suggested_next_step: str = Field(max_length=400)
    transcript_turns: int


def describe(tool: str, result: dict) -> str:
    """One secret-free line per tool result, for the summary and the audit trail."""
    if tool == "check_outage":
        if not result.get("outage_in_area"):
            return "no outage in area"
        return "; ".join(f"{o['service']} outage in {o['area']}, expected fix {o['eta']}" for o in result["outages"])
    if tool == "run_line_test":
        text = (f"{result['verdict']}: SNR {result['snr_db']} dB, {result['errors_15min']} errors/15 min, "
                f"{result['drops_24h']} drops/24 h")
        return text + (" (area outage)" if result.get("note") else "")
    if tool == "get_router_status":
        if result["verdict"] == "rebooting":
            return "rebooting"
        return f"{result['verdict']}: online {result['online']}, config ok {result['config_ok']}"
    if tool == "scan_wifi":
        return "; ".join(f"{b['band']} ch {b['channel']}: {b['interference']} interference "
                         f"({b['neighbour_networks']} networks)" for b in result["bands"])
    if tool == "get_bill_status":
        text = f"account {result['account_status']}, balance {result['balance_eur']} EUR"
        return text + (f", overdue since {result['overdue_since']}" if result.get("overdue_since") else "")
    if tool == "reboot_router":
        return "remote restart started"
    if tool == "create_case":
        return f"case {result['case_ref']}" + (" (already open)" if result.get("already_open") else "")
    if tool == "book_technician":
        return f"technician booked: {result['slot']['label']}"
    if tool == "record_outcome":
        return f"customer says: {result['outcome']}"
    if tool == "search_help":
        return ", ".join(f"{p['doc_id']} §{p['section']}" for p in result.get("passages", [])) or "no article found"
    return result.get("status", "")


def build_summary(session, *, handover_id: str, reason: str, priority: str, customer_view: str,
                  suggested_next_step: str, sentiment: str) -> HandoverSummary:
    def clean(text: str) -> str:
        return redact_text((text or "").strip(), session.secrets, identity_phase=True)[:400]

    log = session.tool_log
    return HandoverSummary(
        handover_id=handover_id, created_at=clock.now().isoformat(timespec="seconds"), priority=priority,
        reason=reason, language=session.message_language,
        customer=Customer(account_no=session.account_no, first_name=session.first_name, plan=session.plan_name,
                          verified=session.verified),
        issue=Issue(category=session.case_category or "unknown", customer_view=clean(customer_view)),
        checks=[Step(tool=e["tool"], result=e["result"]) for e in log if e["tool"] in DIAGNOSTIC_TOOLS],
        actions=[Step(tool=e["tool"], result=e["result"]) for e in log if e["tool"] in ACTION_TOOLS],
        open_case_ref=session.open_case_ref, pending_crm_sync=session.pending_crm_sync,
        kb_sources=list(dict.fromkeys(session.kb_sources)), sentiment=sentiment,
        suggested_next_step=clean(suggested_next_step), transcript_turns=session.turn)


def summary_problems(summary: dict, secrets: set = frozenset()) -> list[str]:
    """Completeness rules used by tests and evals: an empty list means the summary is complete."""
    problems = [f"missing {key}" for key in HandoverSummary.model_fields if key not in summary]
    text = json.dumps(summary, ensure_ascii=False)
    problems += [f"secret {s!r} in summary" for s in secrets if s and s in text]
    # A case or a visit must rest on evidence from the remote checks.
    if not summary.get("checks") and any(a["tool"] in ("create_case", "book_technician")
                                         for a in summary.get("actions", [])):
        problems.append("a case or visit without any checks")
    return problems
