"""Compensation rules C1-C4 (ARCHITECTURE.md §7.3): every rule, its boundaries, and agreement with INT-01."""
import re

import pytest

from linecare import config
from linecare.policy import compensation as c
from linecare.policy.compensation import no_show_credit, outage_credit, repeat_fault_goodwill

FEE = 27.90  # Fiber 500


def result(r):
    return (r.eligible, r.amount_eur, r.rule_id)


@pytest.mark.parametrize("hours, expected", [
    (3, ("no", 0.0, "C1")),
    (24, ("no", 0.0, "C1")),             # exactly 24 hours does not qualify
    (24.01, ("yes", 1.86, "C1")),        # just over: 2 started days
    (33, ("yes", 1.86, "C1")),
    (48, ("yes", 1.86, "C1")),           # 2 days exactly
    (49, ("yes", 2.79, "C1")),           # 3 started days
    (24 * 15, ("yes", 13.95, "C1")),     # 15 days = 50 %: at the cap, not over it
    (24 * 16, ("yes", 13.95, "C1")),     # over the cap
    (24 * 40, ("yes", 13.95, "C1")),
])
def test_c1_unplanned_outage(hours, expected):
    assert result(outage_credit(monthly_fee=FEE, outage_hours=hours)) == expected


def test_c1_the_cap_is_named_only_when_it_applies():
    assert "capped" in outage_credit(monthly_fee=FEE, outage_hours=24 * 16).explanation
    assert "capped" not in outage_credit(monthly_fee=FEE, outage_hours=49).explanation


@pytest.mark.parametrize("notice, expected", [
    (72, ("no", 0.0, "C1")),
    (48, ("no", 0.0, "C1")),             # announced exactly 48 hours ahead: no credit
    (47, ("yes", 1.86, "C1")),           # announced too late: counts as unplanned
    (None, ("yes", 1.86, "C1")),         # planned but never announced
])
def test_c1_planned_maintenance_needs_48_hours_notice(notice, expected):
    assert result(outage_credit(monthly_fee=FEE, outage_hours=30, planned=True, notice_hours=notice)) == expected


def test_c1_rounds_half_up_to_cents():
    assert outage_credit(monthly_fee=17.90, outage_hours=25).amount_eur == 1.19   # 1.1933...
    assert outage_credit(monthly_fee=19.95, outage_hours=25).amount_eur == 1.33   # 1.33 exactly


def test_c2_technician_no_show():
    assert result(no_show_credit(our_fault=True)) == ("yes", 10.0, "C2")
    assert result(no_show_credit(our_fault=False)) == ("no", 0.0, "C2")


@pytest.mark.parametrize("cases, expected", [
    (2, ("no", 0.0, "C3")),
    (3, ("needs_approval", 15.0, "C3")),
    (7, ("needs_approval", 15.0, "C3")),
])
def test_c3_repeat_fault_goodwill_needs_approval(cases, expected):
    assert result(repeat_fault_goodwill(cases_in_window=cases)) == expected


@pytest.mark.parametrize("calculate", [
    lambda: outage_credit(monthly_fee=FEE, outage_hours=100, suspended=True),
    lambda: no_show_credit(our_fault=True, suspended=True),
    lambda: repeat_fault_goodwill(cases_in_window=5, suspended=True),
])
def test_c4_no_credit_while_suspended(calculate):
    assert result(calculate()) == ("no", 0.0, "C4")


def test_the_internal_policy_document_states_the_same_numbers():
    text = (config.KB_DIR / "internal" / "INT-01.md").read_text(encoding="utf-8")
    sections = dict(re.findall(r"^## (C\d) .*\n((?:.+\n?)+)", text, re.M))
    assert set(sections) == {"C1", "C2", "C3", "C4"}
    assert f"longer than {c.OUTAGE_MIN_HOURS} hours" in sections["C1"]
    assert "1/30 of the monthly fee" in sections["C1"] and "up to 50% of the monthly fee" in sections["C1"]
    assert f"at least {c.PLANNED_NOTICE_HOURS} hours ahead" in sections["C1"]
    assert f"{c.NO_SHOW_CREDIT:.0f}\nEUR" in sections["C2"] or f"{c.NO_SHOW_CREDIT:.0f} EUR" in sections["C2"]
    assert f"{c.REPEAT_CASES} or more cases" in sections["C3"]
    assert f"within {c.REPEAT_WINDOW_DAYS} days" in sections["C3"]
    assert f"up to {c.REPEAT_GOODWILL_MAX:.0f} EUR" in sections["C3"] and '"needs_approval"' in sections["C3"]
    assert "suspended" in sections["C4"]
