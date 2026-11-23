"""Browser tests (Playwright) for the customer view, against the real app and the scripted fake LLM."""
import pytest
from playwright.sync_api import expect

from mocks.scenarios import DEMO_BY_ACCOUNT

pytestmark = pytest.mark.e2e
MARIA = DEMO_BY_ACCOUNT["QN-100003"]


def send(page, text):
    before = page.locator(".msg.bot:not(.typing)").count()
    page.fill("#input", text)
    page.press("#input", "Enter")
    expect(page.locator(".msg.bot:not(.typing)")).to_have_count(before + 1)
    return page.locator(".msg.bot:not(.typing)").last


def test_reply_tool_chips_and_state(page, app_url, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "pin": MARIA.pin})],
                          [("reboot_router", {})], reply="Your router is frozen. Shall I restart it?")
    page.goto(app_url)
    reply = send(page, f"No internet. {MARIA.account_no}, PIN {MARIA.pin}")
    expect(reply).to_contain_text("Shall I restart it?")
    expect(reply.locator(".tool")).to_have_text(["verify_customer", "reboot_router"])
    expect(reply.locator(".tool.needs_confirmation")).to_have_count(1)
    expect(page.locator("#state .badge.verified")).to_have_text("verified")
    expect(page.locator("#state")).to_contain_text("Мария")
    expect(page.locator("#state .pending")).to_contain_text("Restart the customer's QN-Box 4")
    # The chip's tooltip shows the arguments, with the PIN redacted.
    assert MARIA.pin not in reply.locator(".tool").first.get_attribute("title")


def test_model_and_user_text_are_shown_as_text_not_html(page, app_url, fake_lm):
    payload = '<img src=x onerror="window.__xss=1">'
    fake_lm.script(reply=f"You wrote {payload}")
    page.goto(app_url)
    reply = send(page, payload)
    expect(reply).to_contain_text(payload)
    expect(page.locator(".msg.user").last).to_have_text(payload)
    assert page.evaluate("window.__xss") is None
    assert page.locator("#messages img").count() == 0


def test_demo_customers_and_new_conversation(page, app_url, fake_lm):
    page.goto(app_url)
    expect(page.locator("#demo tr")).to_have_count(6)
    expect(page.locator("#demo tr").nth(2)).to_contain_text(MARIA.pin)
    fake_lm.script(reply="Hello!")
    send(page, "Hi")
    page.click("#newChat")
    expect(page.locator(".msg")).to_have_count(1)
    expect(page.locator("#state .badge")).to_have_text("unverified")


def test_shift_enter_adds_a_line_and_errors_are_shown(page, app_url, fake_lm):
    page.goto(app_url)
    page.fill("#input", "first line")
    page.press("#input", "Shift+Enter")
    page.type("#input", "second line")
    expect(page.locator(".msg.user")).to_have_count(0)  # Shift+Enter did not send
    fake_lm.fail_count = 10
    page.press("#input", "Enter")
    expect(page.locator(".msg.user")).to_have_text("first line\nsecond line")
    expect(page.locator(".msg.bot.error")).to_contain_text("technical problem")


def test_source_chips_open_the_passage_as_text(page, app_url, fake_lm, monkeypatch):
    from linecare.kb import retriever

    hostile = '<img src=x onerror="window.__xss=1">'
    passages = [retriever.Passage("PUB-09", "Payment methods", "Cash", f"Pay at a shop. {hostile}", "public",
                                  "policy", "en", 0.9),
                retriever.Passage("PUB-09", "Payment methods", "Cash", "duplicate chunk", "public", "policy", "en",
                                  0.8),
                retriever.Passage("PUB-08", "Bills", "Reactivation", "Within two hours.", "public", "policy", "en",
                                  0.7)]
    monkeypatch.setattr(retriever, "search", lambda *a, **k: retriever.SearchResult(passages, True, 0.9))
    fake_lm.script_rounds([("search_help", {"query": "pay in cash"})],
                          reply="According to our payment guide, you can pay in cash at a shop.")
    page.goto(app_url)
    reply = send(page, "Can I pay in cash?")
    chips = reply.locator(".source")
    expect(chips).to_have_text(["PUB-09 §Cash", "PUB-08 §Reactivation"])  # one chip per doc and section
    chips.first.click()
    expect(reply.locator(".passage")).to_contain_text("Payment methods · Cash")
    expect(reply.locator(".passage")).to_contain_text(hostile)
    assert page.evaluate("window.__xss") is None and page.locator("#messages img").count() == 0
    chips.nth(1).click()  # one passage open at a time
    expect(reply.locator(".passage")).to_have_count(1)
    expect(reply.locator(".passage")).to_contain_text("Within two hours.")
    chips.nth(1).click()
    expect(reply.locator(".passage")).to_have_count(0)


def test_confirmation_buttons_and_case_and_visit_in_the_state(page, app_url, fake_lm, monkeypatch):
    from linecare import session as session_module

    monkeypatch.setattr(session_module._secrets, "token_hex", lambda n: "abc123")
    elena = DEMO_BY_ACCOUNT["QN-100002"]
    fake_lm.script_rounds([("verify_customer", {"account_no": elena.account_no, "pin": elena.pin})],
                          [("run_line_test", {}), ("get_router_status", {})],
                          [("create_case", {"category": "internet_down", "subject": "Line degraded"})],
                          [("book_technician", {"slot_id": "SL-SOF-01-1121-08"})],
                          reply="Shall I book Saturday 08:00-10:00?")
    page.goto(app_url)
    reply = send(page, f"No internet. {elena.account_no} {elena.pin}")
    expect(page.locator("#state")).to_contain_text("Case: 00001001")
    expect(reply.locator(".quick button")).to_have_text(["Yes, go ahead", "No"])

    fake_lm.script_rounds([("book_technician", {"confirmation_id": "CF-ABC123"})], reply="Booked.")
    reply.locator(".quick button").first.click()
    expect(page.locator(".msg.user").last).to_have_text("Yes, go ahead")
    expect(page.locator(".msg.bot:not(.typing)").last).to_have_text("Booked.book_technician")
    expect(page.locator("#state")).to_contain_text("Technician: Saturday 2026-11-21, 08:00-10:00")
    expect(page.locator(".quick")).to_have_count(0)


def test_bulgarian_quick_replies(page, app_url, fake_lm):
    fake_lm.script_rounds([("verify_customer", {"account_no": MARIA.account_no, "pin": MARIA.pin})],
                          [("reboot_router", {})], reply="Да рестартирам ли рутера?")
    page.goto(app_url)
    reply = send(page, f"Нямам интернет. {MARIA.account_no}, ПИН {MARIA.pin}")
    expect(reply.locator(".quick button")).to_have_text(["Да, давай", "Не"])


FAKE_MICROPHONE = """
navigator.mediaDevices.getUserMedia = async () => ({getTracks: () => [{stop() {}}]});
window.MediaRecorder = class {
    constructor() { this.mimeType = 'audio/webm'; }
    start() {}
    stop() { this.ondataavailable({data: new Blob([new Uint8Array(4000)])}); this.onstop(); }
};
HTMLMediaElement.prototype.play = function () { window.__played = this.src; return Promise.resolve(); };
"""


def test_a_voice_message_shows_the_transcript_and_the_reply_is_spoken(page, app_url, fake_lm, monkeypatch):
    import server
    from linecare.voice.tts import FakeTTS

    tts = FakeTTS()
    monkeypatch.setattr(server, "tts_provider", tts)
    monkeypatch.setattr(server, "transcribe_audio", lambda path, language: ("Нямам интернет от сутринта", "bg"))
    fake_lm.script(reply="Съжалявам! Кажете ми номера на акаунта си.")
    page.add_init_script(FAKE_MICROPHONE)
    page.goto(app_url)
    page.check("#voiceReplies")
    page.click("#mic")
    expect(page.locator("#mic.recording")).to_have_count(1)
    page.click("#mic")
    expect(page.locator(".msg.user").last).to_have_text("🎤 Нямам интернет от сутринта")
    expect(page.locator(".msg.bot:not(.typing)").last).to_have_text("Съжалявам! Кажете ми номера на акаунта си.")
    page.wait_for_function("window.__played && window.__played.startsWith('blob:')")
    assert tts.calls == [("Съжалявам! Кажете ми номера на акаунта си.", "bg", "bg-BG-KalinaNeural")]
