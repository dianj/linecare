"""Each customer tool's result and rules, on the seeded scenarios."""
from linecare import config
from linecare.tools.billing import get_bill_status
from linecare.tools.common import normalise_account_no
from linecare.tools.diagnostics import (
    change_wifi_channel,
    get_router_status,
    reboot_router,
    run_line_test,
    scan_wifi,
)
from linecare.tools.network import check_outage


def test_account_numbers_are_normalised():
    assert normalise_account_no("qn-100003") == "QN-100003"
    assert normalise_account_no("QN 100 003") == "QN-100003"
    assert normalise_account_no("100003") == "QN-100003"
    assert normalise_account_no("QN-1003") is None and normalise_account_no(None) is None


def test_outage_for_a_verified_customer_uses_their_own_area(verified):
    result = check_outage(verified("QN-100001"), "internet", city="Varna")  # the city argument is ignored
    assert result["outage_in_area"] is True
    assert result["outages"][0]["area"] == "North Gate, Sofia" and "area_code" not in result["outages"][0]
    assert check_outage(verified("QN-100003"), "internet") == {"status": "ok", "service": "internet",
                                                               "outage_in_area": False, "outages": []}


def test_outage_lookup_for_unverified_callers_needs_a_known_place(new_session):
    session = new_session()
    assert check_outage(session, "internet")["status"] == "error"
    assert check_outage(session, "internet", city="Paris")["cities"] == ["Sofia", "Plovdiv", "Varna"]
    assert check_outage(session, "tv", city="plovdiv", district="canal side")["outage_in_area"] is True
    assert check_outage(session, "radio", city="Sofia")["status"] == "error"


def test_line_test_explains_an_area_outage(verified):
    result = run_line_test(verified("QN-100001"))
    assert result["verdict"] == "no_sync" and "outage" in result["note"]
    degraded = run_line_test(verified("QN-100002"))
    assert degraded["verdict"] == "degraded" and degraded["snr_db"] < 6 and "note" not in degraded


def test_router_status_and_wifi_scan(verified):
    frozen = get_router_status(verified("QN-100003"))
    # Found by the evals: Gemma offered the restart in words only, so the customer had to agree twice.
    assert frozen["verdict"] == "frozen" and "call reboot_router now" in frozen["next_step"]
    assert "next_step" not in get_router_status(verified("QN-100007"))
    scan = scan_wifi(verified("QN-100004"))
    assert scan["bands"][0] == {"band": "2.4 GHz", "channel": 6, "neighbour_networks": 23, "interference": "high"}
    assert "change_wifi_channel" in scan["next_step"]
    assert "next_step" not in scan_wifi(verified("QN-100007"))


def test_bill_status_has_amounts_but_no_payment_details(verified):
    result = get_bill_status(verified("QN-100006"))
    assert result["account_status"] == "suspended" and result["overdue_since"] == "2026-10-20"
    assert result["balance_eur"] == 39.8
    assert set(result) == {"status", "account_status", "balance_eur", "overdue_since", "last_payment"}


def test_reboot_needs_a_confirmation_in_the_next_turn(verified):
    session = verified("QN-100003")
    offer = reboot_router(session)
    assert offer["status"] == "needs_confirmation" and "QN-Box 4" in offer["summary"]
    assert get_router_status(session)["verdict"] == "frozen"  # nothing happened yet
    same_turn = reboot_router(session, confirmation_id=offer["confirmation_id"])
    assert same_turn["reason"] == "confirm_in_next_turn"

    session.begin_turn()  # the customer answered "yes"
    done = reboot_router(session, confirmation_id=offer["confirmation_id"])
    assert done["status"] == "ok" and done["accepted"] is True
    assert get_router_status(session)["verdict"] == "ok"
    assert session.reboots == 1 and session.fixes == [{"tool": "reboot_router", "turn": 2}]


def test_a_made_up_confirmation_id_does_nothing(verified):
    session = verified("QN-100003")
    session.begin_turn()
    assert reboot_router(session, confirmation_id="CF-ABC123")["reason"] == "unknown_or_expired_confirmation"
    assert get_router_status(session)["verdict"] == "frozen"


def test_at_most_two_reboots_per_conversation(verified):
    session = verified("QN-100003")
    for _ in range(config.MAX_REBOOTS_PER_CONVERSATION):
        offer = reboot_router(session)
        session.begin_turn()
        assert reboot_router(session, confirmation_id=offer["confirmation_id"])["status"] == "ok"
    assert reboot_router(session)["reason"] == "reboot_limit"


def test_wifi_channel_change_needs_a_confirmation_and_clears_the_interference(verified):
    session = verified("QN-100004")
    offer = change_wifi_channel(session, band="2.4")
    assert offer["status"] == "needs_confirmation" and "2.4 GHz" in offer["summary"]
    assert scan_wifi(session)["bands"][0]["interference"] == "high"  # nothing happened yet
    session.begin_turn()
    done = change_wifi_channel(session, band="2.4", confirmation_id=offer["confirmation_id"])
    assert (done["status"], done["previous_channel"], done["channel"]) == ("ok", 6, 1)
    assert scan_wifi(session)["bands"][0] == {"band": "2.4 GHz", "channel": 1, "neighbour_networks": 3,
                                              "interference": "low"}
    assert session.fixes == [{"tool": "change_wifi_channel", "turn": 2}]


def test_a_confirmation_given_to_the_wrong_tool_names_the_right_one(verified):
    # Eval s4-channel-after-yes (Gemma, 1 of 2): after "yes, change the channel" the model passed the channel offer's
    # id to reboot_router. It was refused, but as "unknown or expired", so the model made a new offer.
    session = verified("QN-100004")
    offer = change_wifi_channel(session, band="2.4")
    session.begin_turn()
    wrong = reboot_router(session, confirmation_id=offer["confirmation_id"])
    assert wrong["status"] == "blocked" and wrong["reason"] == "confirmation_for_other_tool"
    assert "change_wifi_channel" in wrong["message"] and session.reboots == 0
    assert change_wifi_channel(session, band="2.4", confirmation_id=offer["confirmation_id"])["status"] == "ok"


def test_wifi_channel_change_checks_band_and_channel(verified):
    session = verified("QN-100004")
    assert change_wifi_channel(session, band="6")["status"] == "error"
    offer = change_wifi_channel(session, band="5", channel=13)       # not a 5 GHz channel
    session.begin_turn()
    refused = change_wifi_channel(session, band="5", confirmation_id=offer["confirmation_id"])
    assert refused["status"] == "error" and "auto" in refused["message"]
    offer = change_wifi_channel(session, band="5 GHz", channel="40")
    session.begin_turn()
    assert change_wifi_channel(session, band="5", confirmation_id=offer["confirmation_id"])["channel"] == 40
