"""Secrets redaction: PINs and dates of birth in typed and spoken forms."""
import json

import pytest

from linecare.redaction import REDACTED, redact_arguments, redact_history, redact_text


@pytest.mark.parametrize("text", [
    "My PIN is 1590", "pin: 1 5 9 0", "It's one five nine zero", "ПИН-ът ми е едно пет девет нула",
    "born 1991-06-27", "роден съм на 27.06.1991", "27/6/1991", "27 June 1991", "June 27, 1991", "27 юни 1991",
])
def test_identity_phase_patterns(text):
    redacted = redact_text(text, identity_phase=True)
    assert REDACTED in redacted
    assert not any(ch.isdigit() for ch in redacted.replace("PIN-", ""))


def test_account_numbers_and_ordinary_text_survive():
    text = "Account QN-100003, router since 2 days, about 15 minutes ago"
    assert redact_text(text, identity_phase=True) == text
    assert redact_text("My account is 100003", identity_phase=True) == "My account is 100003"


def test_known_secrets_are_removed_after_the_identity_phase_too():
    secrets = {"1590", "1991-06-27"}
    assert redact_text("PIN 1590, born 27.06.1991", secrets) == f"PIN {REDACTED}, born {REDACTED}"
    assert redact_text("one five nine zero", secrets) == REDACTED
    # Outside the identity phase, numbers that are not secrets stay: "since 2019", "SNR 4.1".
    assert redact_text("since 2019, two three", secrets) == "since 2019, two three"


def test_verify_arguments_are_redacted_and_other_tools_untouched():
    assert redact_arguments("verify_customer", {"account_no": "QN-100003", "pin": "1590"}) == {
        "account_no": "QN-100003", "pin": REDACTED}
    assert redact_arguments("check_outage", {"city": "Sofia"}) == {"city": "Sofia"}


def test_history_redaction_covers_user_text_assistant_text_and_tool_arguments():
    history = [
        {"role": "user", "content": "QN-100003, PIN 1590"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "verify_customer", "arguments": json.dumps({"account_no": "QN-100003", "pin": "1590"})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status": "ok", "verified": true}'},
        {"role": "assistant", "content": "Thanks, PIN 1590 is right."},
        {"role": "user", "content": "The internet has been down since 2019? no, since today"},
    ]
    redact_history(history, {"1590"}, identity_phase_upto=1)
    assert "1590" not in json.dumps(history)
    assert json.loads(history[1]["tool_calls"][0]["function"]["arguments"])["account_no"] == "QN-100003"
    assert "2019" in history[4]["content"]  # sent after verification: only known secrets are removed
