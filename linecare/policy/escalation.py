"""Handover priority (spec §6.3). The customer may ask for a human at any time; that is never refused."""

HANDOVER_REASONS = ("customer_request", "cannot_resolve", "config_required", "repeat_contact", "angry_customer",
                    "security_lock", "out_of_scope")
HIGH_PRIORITY_REASONS = {"repeat_contact", "angry_customer", "security_lock"}
REPEAT_CASES = 2          # this many cases on the same category ...
REPEAT_WINDOW_DAYS = 30   # ... within this many days make the customer a repeat contact


def is_repeat_contact(recent_cases: list, category: str | None) -> bool:
    """`recent_cases`: CaseSummary items from the last REPEAT_WINDOW_DAYS days."""
    return bool(category) and sum(c.category == category for c in recent_cases) >= REPEAT_CASES


def handover_priority(reason: str, *, repeat_contact: bool = False, sentiment: str = "neutral") -> str:
    if reason in HIGH_PRIORITY_REASONS or repeat_contact or sentiment == "angry":
        return "high"
    return "normal"
