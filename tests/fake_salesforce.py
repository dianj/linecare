"""A small in-process Salesforce REST API for the contract tests (an `httpx.MockTransport` handler).

It covers what SalesforceCRM uses: the client-credentials token endpoint, version discovery, SOQL queries of the
shapes the adapter sends (WHERE with =, >= and IN joined by AND, ORDER BY, LIMIT; string literals with escapes),
upsert by external id, create, update and delete. Unknown requests fail loudly, so the adapter cannot drift from it
unnoticed. `revoke_tokens()` and `down` simulate an expired token and an outage.
"""
import itertools
import json
import re
from datetime import datetime

import httpx

from mocks import clock

LOGIN = "https://quillnet-demo.my.salesforce.com"
CLIENT_ID, CLIENT_SECRET = "test-client-id", "test-client-secret"
VERSION = "v66.0"

STRING = r"'((?:[^'\\]|\\.)*)'"
CONDITION = re.compile(rf"^([\w.]+) (=|>=) ({STRING}|[\d\-T:Z]+)$|^([\w.]+) IN \(((?:{STRING}(?:, )?)+)\)$")
QUERY = re.compile(r"^SELECT (.+?) FROM (\w+)(?: WHERE (.+?))?(?: ORDER BY (\w+)( DESC)?)?(?: LIMIT (\d+))?$")
PREFIXES = {"Contact": "003", "Case": "500", "CaseComment": "00a"}


def unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def split_conditions(where: str) -> list[str]:
    """Split on AND outside string literals."""
    parts, current, quoted, i = [], "", False, 0
    while i < len(where):
        ch = where[i]
        if ch == "\\" and quoted:
            current += where[i:i + 2]
            i += 2
            continue
        if ch == "'":
            quoted = not quoted
        if not quoted and where.startswith(" AND ", i):
            parts.append(current)
            current, i = "", i + 5
            continue
        current += ch
        i += 1
    return parts + [current]


class FakeSalesforce:
    def __init__(self):
        self.records = {"Contact": {}, "Case": {}, "CaseComment": {}}
        self.ids = itertools.count(1)
        self.case_numbers = itertools.count(1000)
        self.tokens: set[str] = set()
        self.token_requests = 0
        self.down = False
        self.requests: list[str] = []
        self.page_size = 2000  # Salesforce's default batch; tests lower it to exercise nextRecordsUrl
        self.cursors: dict[str, list] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def revoke_tokens(self):
        self.tokens.clear()

    # --- dispatch -----------------------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Salesforce is down", request=request)
        path = request.url.path
        self.requests.append(f"{request.method} {path}")
        if path == "/services/oauth2/token":
            return self.token(request)
        if request.headers.get("Authorization", "").removeprefix("Bearer ") not in self.tokens:
            return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID", "message": "Session expired"}])
        if path == "/services/data/":
            return httpx.Response(200, json=[{"version": "65.0"}, {"version": VERSION[1:]}])
        prefix = f"/services/data/{VERSION}/"
        assert path.startswith(prefix), path
        rest = path.removeprefix(prefix).split("/")
        body = json.loads(request.content) if request.content else None
        match request.method, rest:
            case "GET", ["query"]:
                return httpx.Response(200, json=self.page(self.query(request.url.params["q"])))
            case "GET", ["query", cursor]:
                return httpx.Response(200, json=self.page(self.cursors.pop(cursor)))
            case "PATCH", ["sobjects", "Contact", "Account_No__c", account_no]:
                return self.upsert_contact(account_no, body)
            case "POST", ["sobjects", sobject]:
                return httpx.Response(201, json={"id": self.insert(sobject, body), "success": True, "errors": []})
            case "PATCH", ["sobjects", sobject, record_id]:
                self.records[sobject][record_id].update(body)
                return httpx.Response(204)
            case "DELETE", ["sobjects", sobject, record_id]:
                self.records[sobject].pop(record_id)
                return httpx.Response(204)
        raise AssertionError(f"FakeSalesforce does not know {request.method} {path}")

    def token(self, request):
        self.token_requests += 1
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        if (form.get("grant_type"), form.get("client_id"), form.get("client_secret")) != (
                "client_credentials", CLIENT_ID, CLIENT_SECRET):
            return httpx.Response(400, json={"error": "invalid_client", "error_description": "invalid client"})
        token = f"token-{self.token_requests}"
        self.tokens.add(token)
        return httpx.Response(200, json={"access_token": token, "instance_url": LOGIN, "token_type": "Bearer",
                                         "issued_at": "0", "scope": "api"})

    # --- records ------------------------------------------------------------------------------------------------

    def insert(self, sobject, body):
        record_id = f"{PREFIXES[sobject]}FS{next(self.ids):010d}"
        record = {"Id": record_id, **body, "CreatedDate": clock.now().strftime("%Y-%m-%dT%H:%M:%S.000+0000")}
        if sobject == "Case":
            record["CaseNumber"] = f"{next(self.case_numbers):08d}"
        if sobject == "CaseComment":
            assert body["ParentId"] in self.records["Case"], "CaseComment.ParentId must be a Case"
        self.records[sobject][record_id] = record
        return record_id

    def upsert_contact(self, account_no, body):
        assert "LastName" in body
        for record in self.records["Contact"].values():
            if record["Account_No__c"] == account_no:
                record.update(body)
                return httpx.Response(200, json={"id": record["Id"], "created": False, "success": True})
        record_id = self.insert("Contact", {**body, "Account_No__c": account_no})
        return httpx.Response(201, json={"id": record_id, "created": True, "success": True})

    # --- SOQL ---------------------------------------------------------------------------------------------------

    def field(self, record, name):
        if name.startswith("Contact."):
            contact = self.records["Contact"].get(record.get("ContactId"), {})
            return contact.get(name.split(".", 1)[1])
        return record.get(name)

    def matches(self, record, condition):
        found = CONDITION.match(condition)
        assert found, f"FakeSalesforce cannot parse the condition {condition!r}"
        if found.group(1):
            name, op, literal, quoted = found.group(1), found.group(2), found.group(3), found.group(4)
            value = self.field(record, name)
            if op == "=":
                return value == (unescape(quoted) if quoted is not None else literal)
            stored = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
            return stored >= datetime.strptime(literal, "%Y-%m-%dT%H:%M:%SZ")
        options = [unescape(v) for v in re.findall(STRING, found.group(6))]
        return self.field(record, found.group(5)) in options

    def query(self, soql):
        found = QUERY.match(soql)
        assert found, f"FakeSalesforce cannot parse {soql!r}"
        fields, sobject, where, order, desc, limit = found.groups()
        rows = list(self.records[sobject].values())
        if where:
            rows = [r for r in rows if all(self.matches(r, c) for c in split_conditions(where))]
        if order:
            rows = sorted(rows, key=lambda r: (r[order], r["Id"]), reverse=bool(desc))
        if limit:
            rows = rows[:int(limit)]
        out = []
        for row in rows:
            record = {"attributes": {"type": sobject}}
            for name in (f.strip() for f in fields.split(",")):
                if "." in name:
                    parent, child = name.split(".", 1)
                    record.setdefault(parent, {})[child] = self.field(row, name)
                else:
                    record[name] = row.get(name)
            out.append(record)
        return out

    def page(self, records):
        head, tail = records[:self.page_size], records[self.page_size:]
        result = {"totalSize": len(records), "done": not tail, "records": head}
        if tail:
            cursor = f"01gFS{next(self.ids):010d}-{self.page_size}"
            self.cursors[cursor] = tail
            result["nextRecordsUrl"] = f"/services/data/{VERSION}/query/{cursor}"
        return result
