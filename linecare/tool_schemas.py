"""The customer agent's tool schemas (names, descriptions, parameters), sent to the model with every request.

The descriptions are instructions the model reads; ruff's line-length check is relaxed for this file only.
"""


def _tool(name, description, properties=None, required=()):
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties or {}, "required": list(required)}}}


CONFIRMATION_ID = {"type": "string", "description": "Only after the customer agreed in their latest message: the confirmation_id from the earlier needs_confirmation result."}

CUSTOMER_TOOLS = [
    _tool("verify_customer",
          "Verify the caller's identity with their account number and EITHER their 4-digit PIN OR their date of birth. Required before any account tool. Never repeat the PIN or date of birth back to the customer.",
          {"account_no": {"type": "string", "description": "Account number from the bill, e.g. QN-100042."},
           "pin": {"type": "string", "description": "4-digit PIN."},
           "dob": {"type": "string", "description": "Date of birth, YYYY-MM-DD."}},
          ["account_no"]),
    _tool("check_outage",
          "Check for known outages. For a verified customer it uses their own area. For an unverified caller give the city and/or district they name.",
          {"service": {"type": "string", "enum": ["internet", "tv", "mobile"]},
           "city": {"type": "string", "description": "Only for unverified callers: Sofia, Plovdiv or Varna."},
           "district": {"type": "string", "description": "Only for unverified callers."}},
          ["service"]),
    _tool("run_line_test",
          "Run a remote test of the customer's fixed line (sync, signal-to-noise ratio, errors, recent drops). Verified customers only."),
    _tool("get_router_status",
          "Read the customer's router status remotely (online, uptime, WAN and configuration, whether it hands out addresses). Verified customers only."),
    _tool("reboot_router",
          "Restart the customer's router remotely. Two steps: call without confirmation_id to get an offer and ask the customer; call with the confirmation_id only after they agree in their next message. At most 2 restarts per conversation.",
          {"confirmation_id": CONFIRMATION_ID}),
    _tool("scan_wifi",
          "Scan the customer's Wi-Fi bands for channel and interference from neighbouring networks. Use it when wired works but Wi-Fi is slow or unstable."),
    _tool("get_bill_status",
          "The customer's account status (active or suspended), open balance, overdue date and last payment. Verified customers only."),
]
