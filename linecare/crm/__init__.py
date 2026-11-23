"""CRM backends behind one interface (ARCHITECTURE.md §9). `get_crm()` returns the configured one."""
from linecare.crm.fake import FakeCRM

_crm = FakeCRM()


def get_crm():
    return _crm


def set_crm(crm) -> None:
    """Swap the backend (tests: a CRM that fails)."""
    global _crm
    _crm = crm
