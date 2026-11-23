"""FastAPI app: the customer chat, model switching, health, and the mock enterprise systems under /mock.

The logic lives in the `linecare` package; this module wires it to HTTP. Run with: uvicorn server:app
"""
import asyncio
import logging
import threading
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel, Field

from linecare import config, db
from linecare.agents.customer import run_customer_turn
from linecare.agents.loop import llm_client
from linecare.language import detect_language
from linecare.lmstudio import ensure_default_model_on_startup, list_available_models, model_state, switch_model
from linecare.session import Session
from mocks.app import router as mock_router
from mocks.scenarios import DEMO_CUSTOMERS, SCENARIOS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("linecare")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the default model in the background so the page is available while LM Studio loads it.
    threading.Thread(target=ensure_default_model_on_startup, daemon=True).start()
    yield


app = FastAPI(title="LineCare: Quillnet support agent", lifespan=lifespan)
app.include_router(mock_router)
db.ensure_database()

SESSION_COOKIE = "linecare_session"
# cookie id -> {"session": Session, "lock": asyncio.Lock}; the oldest sessions are dropped first.
sessions: "OrderedDict[str, dict]" = OrderedDict()
active_turns = 0  # turns waiting for the LLM; a model switch would unload the model they use

TECHNICAL_PROBLEM = {
    "en": "Sorry, I have a technical problem right now. Please try again in a few minutes.",
    "bg": "Извинете, в момента имам технически проблем. Моля, опитайте отново след няколко минути.",
}
MODEL_SWITCHING = {
    "en": "One moment please, the assistant is switching to another AI model. Try again in a minute.",
    "bg": "Момент, моля: асистентът превключва към друг AI модел. Опитайте отново след минута.",
}


def _new_entry() -> dict:
    session = Session(conv_id=db.new_conversation("customer", model_state["active"]))
    return {"session": session, "lock": asyncio.Lock()}


def get_session(request: Request) -> tuple[str, dict]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie in sessions:
        sessions.move_to_end(cookie)
        return cookie, sessions[cookie]
    cookie = uuid.uuid4().hex
    sessions[cookie] = _new_entry()
    while len(sessions) > config.MAX_SESSIONS:
        sessions.popitem(last=False)
    return cookie, sessions[cookie]


def respond(payload: dict, cookie: str, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    response.set_cookie(SESSION_COOKIE, cookie, httponly=True, samesite="lax")
    return response


def public_state(session: Session) -> dict:
    """What the UI's state strip shows: no secrets, no account data beyond the first name."""
    return {"identity": session.state.lower(), "first_name": session.first_name if session.verified else None,
            "pending": [p.summary for p in session.pending.values()], "conversation": session.conv_id}


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=config.MAX_MESSAGE_CHARS)


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return config.INDEX_PATH.read_text(encoding="utf-8")


@app.post("/api/customer/chat")
async def customer_chat(body: ChatRequest, request: Request):
    cookie, entry = get_session(request)
    session, text = entry["session"], body.text.strip()
    language = detect_language(text)
    if not text:
        return respond({"error_code": "empty", "message": "Type a message."}, cookie, 400)
    if entry["lock"].locked():
        return respond({"error_code": "busy", "message": "Please wait for the previous reply."}, cookie, 409)
    if model_state["switching"]:
        return respond({"reply": MODEL_SWITCHING[language], "tool_calls": [], "system_error": True,
                        "state": public_state(session)}, cookie)

    global active_turns
    async with entry["lock"]:
        active_turns += 1
        model = model_state["active"]
        try:
            result = await run_in_threadpool(run_customer_turn, session, text, model)
        except (APIConnectionError, APITimeoutError):
            log.error("LM Studio is not reachable at %s", config.LLM_BASE_URL)
            result = None
        except Exception:
            log.exception("Customer turn failed")
            result = None
        finally:
            active_turns -= 1

    if result is None:
        return respond({"reply": TECHNICAL_PROBLEM[language], "tool_calls": [], "system_error": True,
                        "state": public_state(session)}, cookie)
    if result.cap_reached:
        log.warning("Tool loop limit reached in %s", session.conv_id)
    tool_calls = [{"name": c["name"], "arguments": c["arguments"], "status": c["status"]} for c in result.tool_calls]
    return respond({"reply": result.reply, "tool_calls": tool_calls, "model": model, "state": public_state(session)},
                   cookie)


@app.post("/api/customer/reset")
def customer_reset(request: Request):
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie in sessions and sessions[cookie]["lock"].locked():
        return respond({"error_code": "busy", "message": "Please wait for the previous reply."}, cookie, 409)
    cookie = cookie or uuid.uuid4().hex
    sessions[cookie] = _new_entry()
    return respond({"status": "ok", "state": public_state(sessions[cookie]["session"])}, cookie)


@app.get("/api/customer/state")
def customer_state(request: Request):
    cookie, entry = get_session(request)
    return respond({"state": public_state(entry["session"])}, cookie)


@app.get("/api/demo-customers")
def demo_customers():
    """Fictional demo accounts for the presenter (the UI shows them; nothing is filled in automatically)."""
    return {"customers": [{"account_no": c.account_no, "name": f"{c.first_name} {c.last_name}", "pin": c.pin,
                           "scenario": c.scenario, "description": SCENARIOS[c.scenario]}
                          for c in DEMO_CUSTOMERS if c.scenario in {"S1", "S2", "S3", "S4", "S6", "S7"}]}


@app.get("/api/health")
def health():
    active = model_state["active"]
    try:
        models = [m.id for m in llm_client.with_options(timeout=3, max_retries=0).models.list().data]
        llm = {"reachable": True, "model_available": active in models}
    except Exception:
        llm = {"reachable": False, "model_available": False}
    return {"llm": llm, "model": active, "switching": model_state["switching"]}


@app.get("/api/models")
def models():
    return {"models": list_available_models(), "active": model_state["active"],
            "switching": model_state["switching"], "target": model_state["target"]}


@app.post("/api/model")
def select_model(model: str = Form(...)):
    # Sync endpoint: FastAPI runs it in a worker thread, so the long LM Studio load does not block the server.
    if model not in {model_id for model_id, _ in config.ALLOWED_MODELS}:
        return JSONResponse({"error_code": "unknown_model", "message": f"Unknown model '{model}'."}, status_code=400)
    if model_state["switching"] or active_turns:
        return JSONResponse({"error_code": "busy", "message": "Try again in a moment."}, status_code=409)
    result = switch_model(model)
    return JSONResponse(result, status_code=200 if result["status"] == "success" else 500)
