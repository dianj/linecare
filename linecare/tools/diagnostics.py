"""Remote diagnostics on the verified customer's line and router. The reboot is a two-step action."""
from linecare import config
from linecare.tools.common import blocked, ok, two_step
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
    return ok(**systems.wifi_scan(session.account_no))
