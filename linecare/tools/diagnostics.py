"""Remote diagnostics on the verified customer's line, router and Wi-Fi. Reboot and channel change are two-step."""
from linecare import config
from linecare.tools.common import blocked, error, ok, two_step
from mocks import service as systems


def run_line_test(session) -> dict:
    result = systems.line_test(session.account_no)
    if result.pop("area_outage"):
        result["note"] = "There is an active internet outage in the customer's area; the line test cannot pass."
    return ok(**result)


NEXT_STEPS = {
    "frozen": "A remote restart usually fixes this. To offer it, call reboot_router now without confirmation_id; "
              "it prepares the offer you then ask the customer about.",
    "rebooting": "The router is restarting. Check again in a moment before telling the customer it works.",
    # S5: found when a customer reset the router to factory settings. Only staff can push the configuration.
    # Found by the evals: in Bulgarian, Gemma replied "I will open a case and transfer you" and called neither.
    "config_error": "The router's internet configuration is wrong (typical after a factory reset). A restart does "
                    "not fix it and you cannot change it. Do both now, in this turn, before you reply: create_case "
                    "(category router), then handover_to_human with reason config_required. Then tell the customer "
                    "that a colleague will set the router up remotely. Do not say you 'will' do it.",
}


def get_router_status(session) -> dict:
    status = systems.router_status(session.account_no)
    if status["verdict"] in NEXT_STEPS:
        status["next_step"] = NEXT_STEPS[status["verdict"]]
    return ok(**status)


def reboot_router(session, confirmation_id: str = None) -> dict:
    if session.reboots >= config.MAX_REBOOTS_PER_CONVERSATION:
        return blocked("reboot_limit", f"The router was already restarted {session.reboots} times in this "
                       "conversation. Do not restart it again; continue the diagnosis or offer further help.")
    summary = (f"Restart the customer's {session.router_model} remotely. Internet, Wi-Fi and TV will be down "
               "for about two minutes.")

    def execute(_arguments):
        result = systems.reboot_router(session.account_no)
        if not result["accepted"]:
            return blocked(result["reason"], "The router is restarting already. Wait and check its status.")
        session.reboots += 1
        session.record_fix("reboot_router")
        return ok(accepted=True, back_in_s=result["expected_back_in_s"],
                  message="The restart was started. Check get_router_status before telling the customer it is "
                          "fixed, then ask them whether it works now.")

    return two_step(session, "reboot_router", {}, summary, confirmation_id, execute)


def scan_wifi(session) -> dict:
    result = systems.wifi_scan(session.account_no)
    if any(b["interference"] == "high" for b in result["bands"]):
        result["next_step"] = ("A crowded band: offer change_wifi_channel (call it now without confirmation_id to "
                               "prepare the offer) and explain the Wi-Fi tips from search_help.")
    return ok(**result)


def change_wifi_channel(session, band: str, channel="auto", confirmation_id: str = None) -> dict:
    band_key = str(band).replace("GHz", "").strip()
    if band_key not in systems.WIFI_CHANNELS:
        return error("band must be '2.4' or '5'.")
    channel = "auto" if channel in (None, "") else channel
    summary = (f"Move the customer's {band_key} GHz Wi-Fi to "
               f"{'a less crowded channel' if str(channel).lower() == 'auto' else f'channel {channel}'}. Devices "
               "on that band disconnect for a few seconds and reconnect by themselves.")

    def execute(arguments):
        result = systems.change_wifi_channel(session.account_no, arguments["band"], arguments["channel"])
        if not result["changed"]:
            return error(f"That channel cannot be used ({result['reason']}). Use 'auto'.")
        session.record_fix("change_wifi_channel")
        return ok(**result, message="Done. Ask the customer to test the Wi-Fi again and say whether it is better.")

    return two_step(session, "change_wifi_channel", {"band": band_key, "channel": channel}, summary,
                    confirmation_id, execute)
