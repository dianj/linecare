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
    _tool("change_wifi_channel",
          "Move one Wi-Fi band of the customer's router to a less crowded channel (when scan_wifi shows high interference). Two steps: call without confirmation_id to get an offer and ask the customer; call with the confirmation_id only after they agree in their next message.",
          {"band": {"type": "string", "enum": ["2.4", "5"]},
           "channel": {"type": "string", "description": "'auto' (recommended) or a channel number."},
           "confirmation_id": CONFIRMATION_ID},
          ["band"]),
    _tool("get_bill_status",
          "The customer's account status (active or suspended), open balance, overdue date and last payment. Verified customers only."),
    _tool("search_help",
          "Search Quillnet's public help articles (bills and payment, technician visits, Wi-Fi, router lights and restarts, outages, no-internet steps). Use it for any question about rules, terms or how-to steps, in any language. Returns passages and whether the articles answer the question.",
          {"query": {"type": "string", "description": "The question in a few words, e.g. 'pay bill in cash'."}},
          ["query"]),
    _tool("create_case",
          "Open a support case in the CRM for a fault that needs follow-up (a technician visit always needs one). One open case per issue: if one exists, it is returned. Not for problems a known outage explains.",
          {"category": {"type": "string", "enum": ["internet_down", "slow_internet", "wifi", "intermittent", "tv", "billing", "router", "other"]},
           "subject": {"type": "string", "description": "Short subject, e.g. 'No internet, line test degraded'."},
           "description": {"type": "string", "description": "The problem in the customer's words, one or two sentences."},
           "diagnostics_summary": {"type": "string", "description": "What the checks showed, one sentence."}},
          ["category", "subject"]),
    _tool("book_technician",
          "Book a technician visit; allowed only when the checks found a fault (the system decides and explains a refusal). Needs an open case. Call without arguments to get free windows; call with the chosen slot_id to get a confirmation offer; call with confirmation_id only after the customer agrees in their next message.",
          {"slot_id": {"type": "string", "description": "A slot_id from the list of free windows."},
           "confirmation_id": CONFIRMATION_ID}),
    _tool("handover_to_human",
          "Hand the conversation to a human colleague with a summary, so the customer does not repeat anything. Use it at once when the customer asks for a person, and when you cannot solve the problem. Allowed also before verification.",
          {"reason": {"type": "string", "enum": ["customer_request", "cannot_resolve", "config_required", "repeat_contact", "angry_customer", "security_lock", "out_of_scope"]},
           "customer_view": {"type": "string", "description": "One sentence: the problem as the customer describes it. No PIN or date of birth."},
           "suggested_next_step": {"type": "string", "description": "One sentence for the colleague."},
           "sentiment": {"type": "string", "enum": ["calm", "neutral", "frustrated", "angry"]}},
          ["reason"]),
    _tool("record_outcome",
          "Record whether the fix worked, after you asked 'does it work now?' and the customer answered in their latest message. Read the meaning: 'yes, it still does not work' is not_resolved.",
          {"outcome": {"type": "string", "enum": ["resolved", "not_resolved"]},
           "note": {"type": "string", "description": "Optional, a few words."}},
          ["outcome"]),
]

