"""SalesforceCRM beyond the contract: authentication, token expiry, outages, setup errors and query safety."""
import httpx
import pytest
from fake_salesforce import CLIENT_ID, CLIENT_SECRET, LOGIN, FakeSalesforce

from linecare import config
from linecare.crm import make_crm
from linecare.crm.base import CaseIn, Contact, CRMUnavailable
from linecare.crm.fake import FakeCRM
from linecare.crm.salesforce import SalesforceCRM, soql_string


@pytest.fixture
def api():
    return FakeSalesforce()


@pytest.fixture
def crm(api):
    crm = SalesforceCRM(LOGIN, CLIENT_ID, CLIENT_SECRET, transport=api.transport())
    crm.upsert_contact(Contact("QN-100002", "Elena", "Markova"))
    return crm


def test_one_token_is_reused_and_the_newest_api_version_is_used(api, crm):
    crm.find_contact("QN-100002")
    crm.find_contact("QN-100002")
    assert api.token_requests == 1
    assert crm.api_version == "v66.0"
    assert api.requests.count("GET /services/data/") == 1


def test_a_configured_api_version_is_not_discovered(api):
    crm = SalesforceCRM(LOGIN, CLIENT_ID, CLIENT_SECRET, api_version="v66.0", transport=api.transport())
    crm.find_contact("QN-100002")
    assert "GET /services/data/" not in api.requests


def test_an_expired_token_is_renewed_once(api, crm):
    api.revoke_tokens()
    assert crm.find_contact("QN-100002").last_name == "Markova"
    assert api.token_requests == 2


def test_wrong_credentials_are_reported_as_crm_unavailable(api):
    crm = SalesforceCRM(LOGIN, CLIENT_ID, "wrong-secret", transport=api.transport())
    with pytest.raises(CRMUnavailable, match="login failed"):
        crm.find_contact("QN-100002")


def test_an_outage_is_reported_as_crm_unavailable(api, crm):
    api.down = True
    with pytest.raises(CRMUnavailable, match="unreachable"):
        crm.create_case(CaseIn("QN-100002", "wifi", "s", "d", "x"))


def test_a_login_outage_is_reported_as_crm_unavailable(api):
    api.down = True
    with pytest.raises(CRMUnavailable, match="login failed"):
        SalesforceCRM(LOGIN, CLIENT_ID, CLIENT_SECRET, transport=api.transport()).find_contact("QN-100002")


@pytest.mark.parametrize("status, message", [(503, "error 503"), (400, "rejected the request")])
def test_server_errors_and_setup_errors_are_crm_unavailable(api, crm, status, message):
    """400 means a missing custom field or object in the org: a setup error, not a customer problem."""
    original = api.handle
    api.handle = lambda request: (httpx.Response(status, json=[{"errorCode": "INVALID_FIELD"}])
                                  if "/query" in request.url.path else original(request))
    crm._http = httpx.Client(transport=httpx.MockTransport(api.handle))
    with pytest.raises(CRMUnavailable, match=message):
        crm.find_contact("QN-100002")


def test_values_cannot_change_a_query(crm):
    assert soql_string("x' OR Name != '") == "'x\\' OR Name != \\''"
    assert soql_string("a\\") == "'a\\\\'"
    assert crm.find_contact("QN-100002' OR Account_No__c != '") is None
    assert crm.list_cases("QN-100002' OR Status != '", since_days=30) == []


def test_large_results_are_read_page_by_page(api, crm):
    for category in ("internet_down", "wifi", "tv"):
        crm.create_case(CaseIn("QN-100002", category, "s", "d", "x"))
    api.page_size = 1
    assert len(crm.list_cases("QN-100002", since_days=30)) == 3
    assert sum("/query/" in r for r in api.requests) == 2


def test_the_backend_follows_the_setting(monkeypatch):
    assert isinstance(make_crm("fake"), FakeCRM)
    monkeypatch.setattr(config, "SF_LOGIN_URL", LOGIN)
    assert isinstance(make_crm("salesforce"), SalesforceCRM)   # no network until the first call
