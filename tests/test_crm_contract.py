"""The CRM contract: the same tests run against every backend.

- `fake`: FakeCRM on SQLite (what the app uses by default).
- `salesforce`: SalesforceCRM against an in-process fake of the Salesforce REST API (tests/fake_salesforce.py).
- `salesforce-live`: SalesforceCRM against a real org, only with SF_LIVE_TESTS=1 and the SF_* settings in the
  environment. Each test uses a new account number and deletes what it created.
"""
import os
import uuid

import pytest

from linecare.crm.base import CaseIn, Contact
from linecare.crm.fake import FakeCRM
from linecare.crm.salesforce import SalesforceCRM

LIVE = os.environ.get("SF_LIVE_TESTS") == "1"


def fake_salesforce_crm():
    from fake_salesforce import CLIENT_ID, CLIENT_SECRET, LOGIN, FakeSalesforce

    api = FakeSalesforce()
    return SalesforceCRM(LOGIN, CLIENT_ID, CLIENT_SECRET, transport=api.transport())


BACKENDS = [
    pytest.param("fake", id="fake"),
    pytest.param("salesforce", id="salesforce"),
    pytest.param("live", id="salesforce-live", marks=pytest.mark.skipif(not LIVE, reason="SF_LIVE_TESTS=1 not set")),
]


def live_cleanup(crm, account_numbers):
    """Delete the test records (cases first; their comments go with them), then the contacts."""
    for account_no in account_numbers:
        quoted = account_no.replace("'", "\\'")
        for row in crm.query(f"SELECT Id FROM Case WHERE Contact.Account_No__c = '{quoted}'"):
            crm.request("DELETE", f"sobjects/Case/{row['Id']}")
        for row in crm.query(f"SELECT Id FROM Contact WHERE Account_No__c = '{quoted}'"):
            crm.request("DELETE", f"sobjects/Contact/{row['Id']}")


@pytest.fixture(params=BACKENDS)
def backend(request):
    """(crm, account_no of a known contact, account_no nobody has)."""
    kind = request.param
    if kind == "fake":
        crm, account, unknown = FakeCRM(), "QN-100002", "QN-100099"
    else:
        crm = fake_salesforce_crm() if kind == "salesforce" else SalesforceCRM()
        suffix = uuid.uuid4().hex[:6].upper()
        account, unknown = f"QN-T{suffix}", f"QN-U{suffix}"
    crm.upsert_contact(Contact(account, "Elena", "Markova"))
    yield crm, account, unknown
    if kind == "live":
        live_cleanup(crm, [account, unknown])


def case(account, category="internet_down", **overrides):
    fields = dict(account_no=account, category=category, subject="No internet", description="Since morning",
                  diagnostics="run_line_test: degraded")
    return CaseIn(**{**fields, **overrides})


def test_contacts_are_upserted_by_account_number(backend):
    crm, account, unknown = backend
    first = crm.find_contact(account).contact_id
    assert crm.upsert_contact(Contact(account, "Elena", "Markova-Ivanova")) == first
    found = crm.find_contact(account)
    assert (found.first_name, found.last_name) == ("Elena", "Markova-Ivanova")
    assert crm.find_contact(unknown) is None


def test_one_open_case_per_account_and_category(backend):
    crm, account, _ = backend
    first = crm.create_case(case(account))
    again = crm.create_case(case(account, subject="Still no internet"))
    other = crm.create_case(case(account, "wifi"))
    assert first.created and first.status == "New"
    assert not again.created and again.case_ref == first.case_ref
    assert other.created and other.case_ref != first.case_ref
    assert crm.get_open_case(account, "internet_down").case_ref == first.case_ref
    assert crm.get_open_case(account, "tv") is None


def test_case_numbers_are_eight_digits_and_increase(backend):
    crm, account, _ = backend
    first, second = crm.create_case(case(account)).case_ref, crm.create_case(case(account, "wifi")).case_ref
    assert len(first) == len(second) == 8 and first.isdigit() and int(second) > int(first)


def test_a_case_needs_a_known_contact(backend):
    crm, _, unknown = backend
    with pytest.raises(LookupError):
        crm.create_case(case(unknown))


def test_case_details_comments_and_handover_link(backend):
    crm, account, _ = backend
    ref = crm.create_case(case(account)).case_ref
    crm.add_comment(ref, "Technician booked")
    crm.add_comment(ref, "Customer informed", is_public=True)
    crm.link_handover(ref, "HO-0001")
    detail = crm.get_case(ref)
    assert (detail.account_no, detail.category, detail.status) == (account, "internet_down", "Escalated")
    assert (detail.subject, detail.description, detail.diagnostics) == (
        "No internet", "Since morning", "run_line_test: degraded")
    assert [(c["body"], c["is_public"]) for c in detail.comments] == [
        ("Technician booked", False), ("Customer informed", True)]
    assert crm.get_open_case(account, "internet_down").status == "Escalated"   # escalated still counts as open
    assert crm.get_case("99999999") is None
    with pytest.raises(LookupError):
        crm.add_comment("99999999", "x")


def test_recent_cases_are_listed(backend):
    crm, account, unknown = backend
    crm.create_case(case(account))
    crm.create_case(case(account, "wifi"))
    assert {c.category for c in crm.list_cases(account, since_days=30)} == {"internet_down", "wifi"}
    assert crm.list_cases(unknown, since_days=30) == []


def test_fake_case_numbers_start_like_a_new_salesforce_org():
    crm = FakeCRM()
    crm.upsert_contact(Contact("QN-100002", "Elena", "Markova"))
    assert crm.create_case(case("QN-100002")).case_ref == "00001001"
