"""Secrets redaction: PINs and dates of birth never reach the database, the logs or later model rounds.

Two mechanisms:
* **Known secrets**: every PIN or date the model passes to `verify_customer` is remembered by the session and
  replaced everywhere it appears (typed digits, spaced digits, spoken digit words).
* **Identity-phase patterns**: while the caller is not verified, user text is treated as possibly containing
  a PIN or a date of birth: 4-digit numbers, dates and runs of spoken digits are replaced.

The `verify_customer` call itself receives the raw value once; the conversation copy is redacted afterwards
(see `redact_history`).
"""
import json
import re

REDACTED = "[REDACTED]"

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9",
    "нула": "0", "едно": "1", "една": "1", "един": "1", "две": "2", "два": "2", "три": "3", "четири": "4",
    "пет": "5", "шест": "6", "седем": "7", "осем": "8", "девет": "9",
}
_DIGIT_WORD = "(?:" + "|".join(sorted(_DIGIT_WORDS, key=len, reverse=True)) + ")"
_SEP = r"[\s,.\-]*"
# Four or more spoken digits in a row ("one two three four", "едно две три четири").
SPOKEN_DIGITS = re.compile(rf"(?<!\w){_DIGIT_WORD}(?:{_SEP}{_DIGIT_WORD}){{3,}}(?!\w)", re.IGNORECASE)
# A 4-digit group that is not part of a longer number or an account number (QN-100001).
FOUR_DIGITS = re.compile(r"(?<![\d\-])\d(?:[ ]?\d){3}(?![\d\-])")
_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
           "януари|февруари|март|април|май|юни|юли|август|септември|октомври|ноември|декември")
DATES = re.compile(
    r"\b\d{4}-\d{1,2}-\d{1,2}\b"                       # 1986-03-14
    r"|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"            # 14.03.1986, 14/3/86
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}\b"       # 14 March 1986, 14 март 1986
    rf"|\b(?:{_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b",    # March 14, 1986
    re.IGNORECASE)


def secret_forms(value: str) -> set[str]:
    """The ways a secret can appear in text: '1234', '1 2 3 4', '1-2-3-4'; a date also as 14.03.1986."""
    value = str(value).strip()
    forms = {value} if value else set()
    if value.isdigit():
        forms |= {" ".join(value), "-".join(value)}
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if match:
        y, m, d = match.groups()
        forms |= {f"{d}.{m}.{y}", f"{d}/{m}/{y}", f"{int(d)}.{int(m)}.{y}"}
    return forms


def _spoken_value(text: str) -> str:
    return "".join(_DIGIT_WORDS[w.lower()] for w in re.findall(_DIGIT_WORD, text, re.IGNORECASE))


def redact_text(text: str, secrets: set[str] = frozenset(), identity_phase: bool = False) -> str:
    if not text:
        return text
    for secret in sorted({f for s in secrets for f in secret_forms(s)}, key=len, reverse=True):
        text = re.sub(rf"(?<!\d){re.escape(secret)}(?!\d)", REDACTED, text)
    # Spoken digits: redact a run whose value is a known secret, or any run during the identity phase.
    text = SPOKEN_DIGITS.sub(
        lambda m: REDACTED if identity_phase or _spoken_value(m.group(0)) in secrets else m.group(0), text)
    if identity_phase:
        text = DATES.sub(REDACTED, text)
        text = FOUR_DIGITS.sub(REDACTED, text)
    return text


def redact_arguments(tool: str, arguments: dict) -> dict:
    """Tool arguments as they may be stored or shown: verify_customer never keeps its PIN or date of birth."""
    if tool != "verify_customer":
        return arguments
    return {k: (REDACTED if k in ("pin", "dob") and v else v) for k, v in arguments.items()}


def redact_history(history: list, secrets: set[str], identity_phase_upto: int = 0) -> None:
    """Redact a chat history in place.

    Known secrets are removed from every message; user messages before index `identity_phase_upto` (sent while
    the caller was not yet verified) also get the identity-phase patterns. verify_customer arguments are redacted.
    """
    for index, message in enumerate(history):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            message["content"] = redact_text(message["content"], secrets, identity_phase=index < identity_phase_upto)
        elif message.get("role") == "assistant":
            if isinstance(message.get("content"), str):
                message["content"] = redact_text(message["content"], secrets)
            for call in message.get("tool_calls") or []:
                if call["function"]["name"] == "verify_customer":
                    try:
                        arguments = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    if isinstance(arguments, dict):
                        call["function"]["arguments"] = json.dumps(redact_arguments("verify_customer", arguments))
                    else:
                        call["function"]["arguments"] = "{}"
