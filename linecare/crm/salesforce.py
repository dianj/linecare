"""SalesforceCRM: the CRM interface over the Salesforce REST API with plain `httpx` (ARCHITECTURE.md §9).

Authentication is the OAuth 2.0 client-credentials flow of an External Client App: no user signs in, the app acts as
its Run As user. The flow has no refresh token, so the access token is kept in memory and fetched again once when a
call returns 401. The API version is discovered from `/services/data/` unless `SF_API_VERSION` fixes it.

Objects and fields (setup steps in the README, "Salesforce setup"):
- Contact: FirstName, LastName and the external id `Account_No__c` (upsert by account number).
- Case: Subject, Description, Status, Origin, Priority, ContactId and the custom fields `Category__c`,
  `Diagnostics__c`, `Handover_Id__c`. The case reference shown to customers is the CaseNumber.
- CaseComment: ParentId, CommentBody, IsPublished.
"""
import logging
from datetime import timedelta
from urllib.parse import quote

import httpx

from linecare import config
from linecare.crm.base import OPEN_STATUSES, CaseDetail, CaseIn, CaseRef, CaseSummary, Contact, CRMUnavailable
from mocks import clock

log = logging.getLogger(__name__)

# Our field name -> Salesforce field name, in one place.
CONTACT_FIELDS = {"account_no": "Account_No__c", "first_name": "FirstName", "last_name": "LastName"}
CASE_FIELDS = {"subject": "Subject", "description": "Description", "diagnostics": "Diagnostics__c",
               "category": "Category__c", "origin": "Origin", "priority": "Priority"}
CASE_SELECT = ("Id, CaseNumber, Status, Subject, Description, Priority, CreatedDate, Category__c, Diagnostics__c, "
               "Contact.Account_No__c")


def soql_string(value: str) -> str:
    """A quoted SOQL string literal: backslashes and quotes escaped, so a value cannot change the query."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def soql_datetime(value) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class SalesforceCRM:
    def __init__(self, login_url=None, client_id=None, client_secret=None, api_version=None, transport=None):
        self.login_url = (login_url or config.SF_LOGIN_URL).rstrip("/")
        self.client_id = client_id or config.SF_CLIENT_ID
        self.client_secret = client_secret or config.SF_CLIENT_SECRET
        self.api_version = api_version or config.SF_API_VERSION
        self._http = httpx.Client(timeout=config.SF_TIMEOUT_SECONDS, transport=transport)
        self._token = None
        self._instance_url = None

    # --- transport -------------------------------------------------------------------------------------------

    def _authenticate(self):
        try:
            response = self._http.post(f"{self.login_url}/services/oauth2/token", data={
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret})
        except httpx.HTTPError as exc:
            raise CRMUnavailable(f"Salesforce login failed: {exc.__class__.__name__}") from exc
        if response.status_code != 200:
            # The body names the reason (e.g. invalid_client) and never contains the secret.
            log.error("Salesforce token request failed: %s %s", response.status_code, response.text[:300])
            raise CRMUnavailable(f"Salesforce login failed ({response.status_code})")
        body = response.json()
        self._token, self._instance_url = body["access_token"], body["instance_url"].rstrip("/")
        if not self.api_version:
            versions = self._send("GET", f"{self._instance_url}/services/data/", retry=False).json()
            self.api_version = "v" + versions[-1]["version"]

    def _send(self, method, url, retry=True, **kwargs):
        if self._token is None:
            self._authenticate()
        try:
            response = self._http.request(method, url, headers={"Authorization": f"Bearer {self._token}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise CRMUnavailable(f"Salesforce unreachable: {exc.__class__.__name__}") from exc
        if response.status_code == 401 and retry:  # the token expired or was revoked: one new token, one retry
            self._token = None
            return self._send(method, url, retry=False, **kwargs)
        if response.status_code >= 500 or response.status_code in (401, 403):
            log.error("Salesforce %s %s -> %s %s", method, url, response.status_code, response.text[:300])
            raise CRMUnavailable(f"Salesforce error {response.status_code}")
        return response

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """A REST call relative to `/services/data/<version>/` (used by the adapter and the live tests' cleanup)."""
        if self._token is None:
            self._authenticate()
        return self._send(method, f"{self._instance_url}/services/data/{self.api_version}/{path}", **kwargs)

    def _checked(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:  # 400: a field or object is missing or misnamed: a setup error
            log.error("Salesforce request failed: %s %s", response.status_code, response.text[:500])
            raise CRMUnavailable(f"Salesforce rejected the request ({response.status_code})")
        return response

    def query(self, soql: str) -> list[dict]:
        records, response = [], self._checked(self.request("GET", "query", params={"q": soql})).json()
        records += response["records"]
        while not response.get("done", True):
            response = self._checked(self._send("GET", self._instance_url + response["nextRecordsUrl"])).json()
            records += response["records"]
        return records

    # --- the CRM interface ------------------------------------------------------------------------------------

    def find_contact(self, account_no):
        rows = self.query(f"SELECT Id, FirstName, LastName, Account_No__c FROM Contact "
                          f"WHERE Account_No__c = {soql_string(account_no)} LIMIT 1")
        if not rows:
            return None
        row = rows[0]
        return Contact(row["Account_No__c"], row["FirstName"] or "", row["LastName"], row["Id"])

    def upsert_contact(self, contact):
        body = {"FirstName": contact.first_name, "LastName": contact.last_name}
        response = self._checked(self.request(
            "PATCH", f"sobjects/Contact/Account_No__c/{quote(contact.account_no, safe='')}", json=body))
        if response.status_code == 201 or response.content:  # created (or updated, on newer API versions)
            return response.json()["id"]
        return self.find_contact(contact.account_no).contact_id

    def _case_ref(self, row) -> CaseRef:
        return CaseRef(row["CaseNumber"], row["Status"], created=False)

    def get_open_case(self, account_no, category):
        statuses = ", ".join(soql_string(s) for s in OPEN_STATUSES)
        rows = self.query(f"SELECT {CASE_SELECT} FROM Case WHERE Contact.Account_No__c = {soql_string(account_no)} "
                          f"AND Category__c = {soql_string(category)} AND Status IN ({statuses}) "
                          f"ORDER BY CreatedDate DESC LIMIT 1")
        return self._case_ref(rows[0]) if rows else None

    def create_case(self, case: CaseIn):
        """Idempotent per account + category while a case is open: returns the open case instead of a new one."""
        existing = self.get_open_case(case.account_no, case.category)
        if existing:
            return existing
        contact = self.find_contact(case.account_no)
        if contact is None:
            raise LookupError(f"no CRM contact for {case.account_no}")
        body = {sf: getattr(case, ours) for ours, sf in CASE_FIELDS.items()}
        body.update(ContactId=contact.contact_id, Status="New")
        case_id = self._checked(self.request("POST", "sobjects/Case", json=body)).json()["id"]
        row = self._case_row(f"Id = {soql_string(case_id)}")
        return CaseRef(row["CaseNumber"], row["Status"], created=True)

    def _case_row(self, where: str) -> dict | None:
        rows = self.query(f"SELECT {CASE_SELECT} FROM Case WHERE {where} LIMIT 1")
        return rows[0] if rows else None

    def list_cases(self, account_no, since_days):
        since = soql_datetime(clock.now() - timedelta(days=since_days))
        rows = self.query(f"SELECT {CASE_SELECT} FROM Case WHERE Contact.Account_No__c = {soql_string(account_no)} "
                          f"AND CreatedDate >= {since} ORDER BY CreatedDate DESC")
        return [CaseSummary(r["CaseNumber"], r["Category__c"] or "other", r["Subject"] or "", r["Status"],
                            r["CreatedDate"]) for r in rows]

    def get_case(self, case_ref):
        row = self._case_row(f"CaseNumber = {soql_string(case_ref)}")
        if row is None:
            return None
        comments = self.query(f"SELECT CommentBody, IsPublished, CreatedDate FROM CaseComment "
                              f"WHERE ParentId = {soql_string(row['Id'])} ORDER BY CreatedDate")
        return CaseDetail(row["CaseNumber"], row["Category__c"] or "other", row["Subject"] or "", row["Status"],
                          row["CreatedDate"], account_no=(row.get("Contact") or {}).get("Account_No__c") or "",
                          description=row["Description"] or "", diagnostics=row["Diagnostics__c"] or "",
                          priority=row["Priority"] or "Medium",
                          comments=[{"body": c["CommentBody"], "is_public": c["IsPublished"],
                                     "created_at": c["CreatedDate"]} for c in comments])

    def _case_id(self, case_ref) -> str:
        row = self._case_row(f"CaseNumber = {soql_string(case_ref)}")
        if row is None:
            raise LookupError(f"unknown case {case_ref}")
        return row["Id"]

    def add_comment(self, case_ref, text, is_public=False):
        body = {"ParentId": self._case_id(case_ref), "CommentBody": text, "IsPublished": is_public}
        return self._checked(self.request("POST", "sobjects/CaseComment", json=body)).json()["id"]

    def link_handover(self, case_ref, handover_id):
        self._checked(self.request("PATCH", f"sobjects/Case/{self._case_id(case_ref)}",
                                   json={"Handover_Id__c": handover_id, "Status": "Escalated"}))
