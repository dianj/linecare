"""CRM backends behind one interface (ARCHITECTURE.md §9). `get_crm()` returns the configured one."""
from linecare import config
from linecare.crm.fake import FakeCRM


def make_crm(backend: str | None = None):
    if (backend or config.CRM_BACKEND) == "salesforce":
        from linecare.crm.salesforce import SalesforceCRM

        return SalesforceCRM()
    return FakeCRM()


_crm = make_crm()


def get_crm():
    return _crm


def set_crm(crm) -> None:
    """Swap the backend (tests: a CRM that fails)."""
    global _crm
    _crm = crm
