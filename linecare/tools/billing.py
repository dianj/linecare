"""get_bill_status: balance and account status. Amounts and dates only, never payment card data."""
from linecare.tools.common import ok
from mocks import service as systems


def get_bill_status(session) -> dict:
    billing = systems.billing(session.account_no)
    return ok(account_status=billing["account_status"], balance_eur=billing["balance_eur"],
              overdue_since=billing["overdue_since"], last_payment=billing["last_payment"])
