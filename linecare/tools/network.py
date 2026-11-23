"""check_outage: public outage information. Unverified callers can ask by city and district."""
from linecare.tools.common import error, ok
from mocks import service as systems

SERVICES = ("internet", "tv", "mobile")
CITIES = ("Sofia", "Plovdiv", "Varna")


def check_outage(session, service: str = "internet", city: str = None, district: str = None) -> dict:
    if service not in SERVICES:
        return error(f"service must be one of {list(SERVICES)}.")
    if session.verified:
        areas = [session.area_code]  # the customer's own area; nothing to ask
    else:
        if not city and not district:
            return error("The customer is not verified: ask for their city and district, or verify them first.",
                         cities=list(CITIES))
        areas = systems.find_area(city, district)
        if not areas:
            return error("Unknown city or district. Ask the customer to check the name.", cities=list(CITIES))
    found = systems.outages(areas, service)
    for outage in found:
        outage.pop("area_code", None)
    return ok(service=service, outage_in_area=bool(found), outages=found)
