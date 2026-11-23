"""Business rules in code: technician eligibility and escalation priority (ARCHITECTURE.md §6.3)."""
import pytest

from linecare.crm.base import CaseSummary
from linecare.policy.escalation import handover_priority, is_repeat_contact
from linecare.policy.technician import REFUSALS, technician_eligibility

OK_LINE = {"verdict": "ok", "drops_24h": 0}
OK_ROUTER = {"verdict": "ok"}


@pytest.mark.parametrize("line, router, outage, status, expected", [
    ({"verdict": "degraded", "drops_24h": 0}, OK_ROUTER, False, "active", (True, "line_fault")),
    ({"verdict": "no_sync", "drops_24h": 0}, OK_ROUTER, False, "active", (True, "line_fault")),
    (OK_LINE, {"verdict": "offline"}, False, "active", (True, "router_hardware")),
    ({"verdict": "ok", "drops_24h": 3}, OK_ROUTER, False, "active", (True, "intermittent_drops")),
    ({"verdict": "ok", "drops_24h": 2}, OK_ROUTER, False, "active", (False, "no_fault_found")),
    (OK_LINE, {"verdict": "frozen"}, False, "active", (False, "no_fault_found")),  # S3: a restart fixes it
    ({"verdict": "no_sync", "drops_24h": 0}, OK_ROUTER, True, "active", (False, "area_outage")),  # S1
    ({"verdict": "degraded", "drops_24h": 0}, OK_ROUTER, False, "suspended", (False, "account_suspended")),  # S6
    (None, None, False, "active", (False, "diagnostics_required")),
])
def test_technician_eligibility_matrix(line, router, outage, status, expected):
    result = technician_eligibility(line=line, router=router, area_outage=outage, account_status=status)
    assert (result.eligible, result.reason) == expected


def test_staff_can_override_but_not_during_an_outage_or_suspension():
    assert technician_eligibility(line=None, router=None, area_outage=False, account_status="active",
                                  override=True).eligible
    assert not technician_eligibility(line=None, router=None, area_outage=True, account_status="active",
                                      override=True).eligible


def test_every_refusal_has_an_explanation_for_the_model():
    reasons = {"account_suspended", "area_outage", "diagnostics_required", "no_fault_found"}
    assert set(REFUSALS) == reasons


def test_repeat_contact_needs_two_recent_cases_on_the_same_category():
    cases = [CaseSummary("1", "internet_down", "s", "Closed", "2026-11-01"),
             CaseSummary("2", "internet_down", "s", "New", "2026-11-15"),
             CaseSummary("3", "wifi", "s", "New", "2026-11-16")]
    assert is_repeat_contact(cases, "internet_down")
    assert not is_repeat_contact(cases, "wifi") and not is_repeat_contact(cases, None)


@pytest.mark.parametrize("reason, kwargs, expected", [
    ("customer_request", {}, "normal"),
    ("cannot_resolve", {}, "normal"),
    ("repeat_contact", {}, "high"),
    ("angry_customer", {}, "high"),
    ("security_lock", {}, "high"),
    ("customer_request", {"repeat_contact": True}, "high"),
    ("customer_request", {"sentiment": "angry"}, "high"),
])
def test_handover_priority(reason, kwargs, expected):
    assert handover_priority(reason, **kwargs) == expected
