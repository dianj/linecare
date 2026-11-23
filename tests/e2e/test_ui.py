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
