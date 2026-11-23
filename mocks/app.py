"""The mock systems as a REST API under /mock, visible in the app's OpenAPI page (/docs).

The agent's tools call `mocks.service` in-process; these routes exist so the integration surface can be
inspected and called like a real operator's APIs.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mocks import service

router = APIRouter(prefix="/mock", tags=["mock enterprise systems"])


def _or_404(result):
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown account")
    return result


class VerifyRequest(BaseModel):
    account_no: str
    pin: str | None = Field(None, pattern=r"^\d{4}$")
    dob: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class AppointmentRequest(BaseModel):
    account_no: str
    slot_id: str
    reason: str = Field(..., max_length=300)
    case_ref: str | None = None


@router.get("/customers/{account_no}", summary="Customer profile (never the PIN or date of birth)")
def customer(account_no: str):
    return _or_404(service.get_customer(account_no))


@router.post("/customers/verify", summary="Check a PIN or a date of birth")
def verify(body: VerifyRequest):
    return {"verified": service.verify(body.account_no, pin=body.pin, dob=body.dob)}


@router.get("/network/outages", summary="Active outages, optionally by area and service")
def outages(area_code: str | None = None, service_kind: str | None = Query(None, alias="service")):
    return {"outages": service.outages([area_code] if area_code else None, service_kind)}


@router.post("/diagnostics/{account_no}/line-test", summary="Run a line test")
def line_test(account_no: str):
    return _or_404(service.line_test(account_no))


@router.get("/diagnostics/{account_no}/router", summary="Router status")
def router_status(account_no: str):
    return _or_404(service.router_status(account_no))


@router.post("/diagnostics/{account_no}/router/reboot", summary="Reboot the router remotely")
def reboot(account_no: str):
    return _or_404(service.reboot_router(account_no))


@router.get("/diagnostics/{account_no}/wifi-scan", summary="Wi-Fi bands, channels and interference")
def wifi_scan(account_no: str):
    return _or_404(service.wifi_scan(account_no))


@router.get("/billing/{account_no}", summary="Balance, recent bills and account status")
def billing(account_no: str):
    return _or_404(service.billing(account_no))


@router.get("/scheduling/slots", summary="Free technician slots in an area")
def slots(area_code: str, days: int = 14, limit: int = 3):
    return {"slots": service.free_slots(area_code, days, limit)}


@router.post("/scheduling/appointments", summary="Book a technician slot")
def book(body: AppointmentRequest):
    result = service.book_appointment(body.account_no, body.slot_id, body.reason, body.case_ref)
    if result is None:
        raise HTTPException(status_code=409, detail="Slot not available")
    return result


@router.delete("/scheduling/appointments/{appt_id}", summary="Cancel an appointment")
def cancel(appt_id: str):
    if not service.cancel_appointment(appt_id):
        raise HTTPException(status_code=404, detail="Unknown or already cancelled appointment")
    return {"cancelled": True}
