"""Compensation and goodwill (ARCHITECTURE.md §7.3). Pure functions: the helpdesk model only explains the result.

The rules match the internal document kb/internal/INT-01.md (a test checks that both name the same numbers):
- C1 unplanned outage over 24 hours: 1/30 of the monthly fee per started day, at most 50 % of the monthly fee;
  exactly 24 hours does not qualify; planned maintenance announced at least 48 hours ahead gives nothing.
- C2 technician no-show on Quillnet's side: a flat 10 EUR.
- C3 repeat fault (3 or more cases in the same category within 60 days): goodwill up to 15 EUR, approval needed.
- C4 suspended for non-payment: no credit for that period (checked before every other rule).
"""
import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

OUTAGE_MIN_HOURS = 24
OUTAGE_CAP_SHARE = Decimal("0.5")
DAYS_PER_MONTH = 30
PLANNED_NOTICE_HOURS = 48
NO_SHOW_CREDIT = Decimal("10.00")
REPEAT_CASES = 3
REPEAT_WINDOW_DAYS = 60
REPEAT_GOODWILL_MAX = Decimal("15.00")

ELIGIBLE, NOT_ELIGIBLE, NEEDS_APPROVAL = "yes", "no", "needs_approval"


@dataclass(frozen=True)
class Compensation:
    eligible: str            # yes | no | needs_approval
    amount_eur: float        # the credit, or the maximum when approval is needed
    rule_id: str             # C1..C4
    explanation: str         # for the operator; the model rephrases it, it does not change it

    def as_dict(self) -> dict:
        return {"eligible": self.eligible, "amount_eur": self.amount_eur, "rule_id": self.rule_id,
                "explanation": self.explanation}


def eur(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def suspended_refusal(event: str) -> Compensation:
    return Compensation(NOT_ELIGIBLE, 0.0, "C4", f"The account is suspended for non-payment, so no credit is given "
                                                 f"for the {event}.")


def outage_credit(*, monthly_fee: float, outage_hours: float, planned: bool = False,
                  notice_hours: float | None = None, suspended: bool = False) -> Compensation:
    if suspended:
        return suspended_refusal("outage")
    if planned and notice_hours is not None and notice_hours >= PLANNED_NOTICE_HOURS:
        return Compensation(NOT_ELIGIBLE, 0.0, "C1", f"Planned maintenance announced {notice_hours:g} hours ahead "
                                                     f"(at least {PLANNED_NOTICE_HOURS}): no credit.")
    if outage_hours <= OUTAGE_MIN_HOURS:
        return Compensation(NOT_ELIGIBLE, 0.0, "C1", f"The outage lasted {outage_hours:g} hours; a credit needs more "
                                                     f"than {OUTAGE_MIN_HOURS} hours.")
    days = math.ceil(outage_hours / 24)
    fee = Decimal(str(monthly_fee))
    amount = fee / DAYS_PER_MONTH * days
    cap = fee * OUTAGE_CAP_SHARE
    capped = amount > cap
    text = (f"{outage_hours:g} hours = {days} started days x 1/30 of the {eur(fee):.2f} EUR monthly fee"
            + (f", capped at 50 % ({eur(cap):.2f} EUR)." if capped else "."))
    return Compensation(ELIGIBLE, eur(min(amount, cap)), "C1", text)


def no_show_credit(*, our_fault: bool, suspended: bool = False) -> Compensation:
    if suspended:
        return suspended_refusal("missed visit")
    if not our_fault:
        return Compensation(NOT_ELIGIBLE, 0.0, "C2", "The visit was missed for a reason on the customer's side: no "
                                                     "credit.")
    return Compensation(ELIGIBLE, eur(NO_SHOW_CREDIT), "C2", "The technician did not arrive and the reason is on "
                                                             "Quillnet's side: flat credit.")


def repeat_fault_goodwill(*, cases_in_window: int, suspended: bool = False) -> Compensation:
    if suspended:
        return suspended_refusal("repeat fault")
    if cases_in_window < REPEAT_CASES:
        return Compensation(NOT_ELIGIBLE, 0.0, "C3", f"{cases_in_window} cases in this category in "
                                                     f"{REPEAT_WINDOW_DAYS} days; goodwill needs at least "
                                                     f"{REPEAT_CASES}.")
    return Compensation(NEEDS_APPROVAL, eur(REPEAT_GOODWILL_MAX), "C3",
                        f"{cases_in_window} cases in this category in {REPEAT_WINDOW_DAYS} days: goodwill of up to "
                        f"{eur(REPEAT_GOODWILL_MAX):.2f} EUR, only with a team lead's approval.")
