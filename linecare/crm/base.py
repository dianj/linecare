"""The CRM interface the tools use. FakeCRM (SQLite) or SalesforceCRM (REST), see ARCHITECTURE.md §9."""
from dataclasses import dataclass, field
from typing import Protocol

CASE_CATEGORIES = ("internet_down", "slow_internet", "wifi", "intermittent", "tv", "billing", "router", "other")
OPEN_STATUSES = ("New", "Working", "Escalated")


class CRMUnavailable(Exception):
    """The CRM could not be reached; tools turn this into `crm_unavailable`."""


@dataclass
class Contact:
    account_no: str
    first_name: str
    last_name: str
    contact_id: str | None = None


@dataclass
class CaseIn:
    account_no: str
    category: str
    subject: str
    description: str
    diagnostics: str
    origin: str = "Chat"
    priority: str = "Medium"


@dataclass
class CaseRef:
    case_ref: str
    status: str
    created: bool  # False when an open case for the same issue already existed


@dataclass
class CaseSummary:
    case_ref: str
    category: str
    subject: str
    status: str
    created_at: str


@dataclass
class CaseDetail(CaseSummary):
    account_no: str = ""
    description: str = ""
    diagnostics: str = ""
    priority: str = "Medium"
    comments: list = field(default_factory=list)


class CRM(Protocol):
    def find_contact(self, account_no: str) -> Contact | None: ...

    def upsert_contact(self, contact: Contact) -> str: ...

    def create_case(self, case: CaseIn) -> CaseRef: ...

    def get_open_case(self, account_no: str, category: str) -> CaseRef | None: ...

    def list_cases(self, account_no: str, since_days: int) -> list[CaseSummary]: ...

    def get_case(self, case_ref: str) -> CaseDetail | None: ...

    def add_comment(self, case_ref: str, text: str, is_public: bool = False) -> str: ...

    def link_handover(self, case_ref: str, handover_id: str) -> None: ...
