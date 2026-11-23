"""The CRM contract: the same tests run against every backend (Salesforce only with SF_LIVE_TESTS=1, from D5)."""
import pytest

from linecare.crm.base import CaseIn, Contact
from linecare.crm.fake import FakeCRM

BACKENDS = [pytest.param(FakeCRM, id="fake")]


@pytest.fixture(params=BACKENDS)
def crm(request):
    backend = request.param()
    backend.upsert_contact(Contact("QN-100002", "Elena", "Markova"))
    return backend


def case(category="internet_down", **overrides):
    fields = dict(account_no="QN-100002", category=category, subject="No internet", description="Since morning",
                  diagnostics="run_line_test: degraded")
    return CaseIn(**{**fields, **overrides})


def test_contacts_are_upserted_by_account_number(crm):
    first = crm.find_contact("QN-100002").contact_id
    assert crm.upsert_contact(Contact("QN-100002", "Elena", "Markova-Ivanova")) == first
    assert crm.find_contact("QN-100002").last_name == "Markova-Ivanova"
    assert crm.find_contact("QN-999999") is None


def test_one_open_case_per_account_and_category(crm):
    first = crm.create_case(case())
    again = crm.create_case(case(subject="Still no internet"))
    other = crm.create_case(case("wifi"))
    assert first.created and not again.created and again.case_ref == first.case_ref
    assert other.created and other.case_ref != first.case_ref
    assert crm.get_open_case("QN-100002", "internet_down").case_ref == first.case_ref
    assert crm.get_open_case("QN-100002", "tv") is None


def test_case_numbers_look_like_salesforce_case_numbers(crm):
    assert crm.create_case(case()).case_ref == "00001001"
    assert crm.create_case(case("wifi")).case_ref == "00001002"


def test_a_case_needs_a_known_contact(crm):
    with pytest.raises(LookupError):
        crm.create_case(case(account_no="QN-100099"))


def test_case_details_comments_and_handover_link(crm):
    ref = crm.create_case(case()).case_ref
    crm.add_comment(ref, "Technician booked")
    crm.link_handover(ref, "HO-0001")
    detail = crm.get_case(ref)
    assert detail.diagnostics == "run_line_test: degraded" and detail.status == "Escalated"
    assert [c["body"] for c in detail.comments] == ["Technician booked"]
    assert crm.get_case("99999999") is None
    with pytest.raises(LookupError):
        crm.add_comment("99999999", "x")


def test_recent_cases_are_listed_newest_first(crm):
    crm.create_case(case())
    crm.create_case(case("wifi"))
    assert {c.category for c in crm.list_cases("QN-100002", since_days=30)} == {"internet_down", "wifi"}
    assert crm.list_cases("QN-100003", since_days=30) == []
