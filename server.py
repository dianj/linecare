"""FastAPI app: the customer chat (text or voice), the operator console and helpdesk chat for staff,
text-to-speech, model switching, health, and the mock enterprise systems under /mock.

The logic lives in the `linecare` package; this module wires it to HTTP. Run with: uvicorn server:app
"""
import asyncio
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel, Field

from linecare import config, db, handover_queue, metrics, ratelimit
from linecare.agents.customer import run_customer_turn
from linecare.agents.helpdesk import run_assist, run_helpdesk_turn
from linecare.agents.loop import llm_client
from linecare.kb import retriever
from linecare.language import detect_language
from linecare.lmstudio import ensure_default_model_on_startup, list_available_models, model_state, switch_model
from linecare.session import Session
from linecare.staff import OPERATOR, StaffSession, role_for_token
from linecare.voice.speech_text import clean_for_speech
from linecare.voice.stt import AudioTooLong, transcribe_audio
from linecare.voice.tts import make_tts
from mocks.app import router as mock_router
from mocks.scenarios import DEMO_CUSTOMERS, SCENARIOS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("linecare")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the default model in the background so the page is available while LM Studio loads it.
    threading.Thread(target=ensure_default_model_on_startup, daemon=True).start()
    # Build or update the KB index (embedding model download on the very first start) without blocking the page.
    threading.Thread(target=_warm_kb, daemon=True).start()
    # Cases that could not be written while the CRM was down (kept in the handover summaries).
    threading.Thread(target=_sync_pending_cases, daemon=True).start()
    yield


def _sync_pending_cases():
    try:
        handover_queue.sync_pending_cases()
    except Exception:
        log.exception("Retrying pending CRM writes failed")


kb_state = {"ready": False, "chunks": None, "error": None}   # set by the start-up warm-up, shown by /api/health


def _warm_kb():
    try:
        index = retriever.get_index()
        kb_state.update(ready=True, chunks=index.collection.count(), error=None)
    except Exception as exc:
        kb_state.update(ready=False, chunks=None, error=type(exc).__name__)
        log.exception("Knowledge base index could not be built; search_help will report a system error")


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
NOT_UNDERSTOOD = {
    "en": "Sorry, I did not catch that. Could you say it again, or type it?",
    "bg": "Извинете, не ви разбрах. Може ли да повторите или да напишете съобщението?",
}
MODEL_SWITCHING = {
    "en": "One moment please, the assistant is switching to another AI model. Try again in a minute.",
    "bg": "Момент, моля: асистентът превключва към друг AI модел. Опитайте отново след минута.",
}


SPOKEN_REPLIES = 20   # replies per session that can still be spoken (the latest ones)


def _new_entry() -> dict:
    session = Session(conv_id=db.new_conversation("customer", model_state["active"]))
    # replies: turn id -> {"text", "language", "audio": {voice: mp3}}; only these can be spoken (POST /api/tts/{id})
    return {"session": session, "lock": asyncio.Lock(), "replies": OrderedDict()}


def remember_reply(entry: dict, text: str, language: str) -> str:
    """Keep a reply the server produced, so the voice output can speak it (and only it) by its turn id."""
    turn_id = uuid.uuid4().hex
    entry["replies"][turn_id] = {"text": text, "language": language, "audio": {}}
    while len(entry["replies"]) > SPOKEN_REPLIES:
        entry["replies"].popitem(last=False)
    return turn_id


limiters = {"chat": ratelimit.RateLimiter(config.CHAT_PER_MINUTE),
            "login": ratelimit.RateLimiter(config.LOGIN_PER_MINUTE)}


def rate_limited(request: Request, kind: str) -> JSONResponse | None:
    """429 when this client address has used up its requests of this kind for now; None otherwise."""
    limiter = limiters[kind]
    if limiter.allow(request.client.host if request.client else "unknown"):
        return None
    return JSONResponse({"error_code": "rate_limited", "message": "Too many requests. Please wait a moment."},
                        status_code=429, headers={"Retry-After": str(limiter.retry_after())})


def blank_state() -> dict:
    """What a browser without a session sees; nothing is created for it (polling cannot push sessions out)."""
    return public_state(Session(conv_id=""))


def get_session(request: Request) -> tuple[str, dict]:
    """This browser's session; a new one only for a real turn (chat or voice), which is rate limited."""
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
    return {"identity": session.state.lower(), "first_name": session.first_name,
            "pending": [p.summary for p in session.pending.values()], "conversation": session.conv_id,
            "case_ref": session.open_case_ref,
            "appointment": session.appointment["label"] if session.appointment else None,
            "handover_id": session.handover_id, "outcome": session.outcome, "language": session.message_language}


PASSAGE_WRAPPER = re.compile(r"<<passage [^>]*>>\n?|\n?<</passage>>")


def ui_tool_call(call: dict) -> dict:
    """A tool call for the UI chips; search_help also carries its sources (from the tool result, not the reply)."""
    entry = {"name": call["name"], "arguments": call["arguments"], "status": call["status"]}
    if call["name"] == "search_help" and call["status"] == "ok":
        entry["sources"] = [{"doc_id": p["doc_id"], "section": p["section"], "title": p["title"],
                             "text": PASSAGE_WRAPPER.sub("", p["text"])} for p in call["result"]["passages"]]
    return entry


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=config.MAX_MESSAGE_CHARS)


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    return config.INDEX_PATH.read_text(encoding="utf-8")


async def run_turn(request: Request, text: str, *, channel: str = "chat", stt_ms: int = None,
                   extra: dict = None) -> JSONResponse:
    """One customer turn for typed or transcribed text; the same rules for both channels."""
    cookie, entry = get_session(request)
    session, extra = entry["session"], extra or {}
    language = extra.get("spoken_language") or detect_language(text)
    if not text:
        return respond({"error_code": "empty", "message": "Type a message."}, cookie, 400)
    if entry["lock"].locked():
        return respond({"error_code": "busy", "message": "Please wait for the previous reply."}, cookie, 409)
    if model_state["switching"]:
        return respond({"reply": MODEL_SWITCHING[language], "tool_calls": [], "system_error": True,
                        "turn_id": remember_reply(entry, MODEL_SWITCHING[language], language),
                        "state": public_state(session), **extra}, cookie)

    global active_turns
    async with entry["lock"]:
        active_turns += 1
        model = model_state["active"]
        try:
            result = await run_in_threadpool(run_customer_turn, session, text, model, channel=channel,
                                             stt_ms=stt_ms)
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
                        "turn_id": remember_reply(entry, TECHNICAL_PROBLEM[language], language),
                        "state": public_state(session), **extra}, cookie)
    if result.cap_reached:
        log.warning("Tool loop limit reached in %s", session.conv_id)
    tool_calls = [ui_tool_call(c) for c in result.tool_calls]
    reply_language = extra.get("spoken_language") or session.message_language or language
    return respond({"reply": result.reply, "tool_calls": tool_calls, "model": model, "state": public_state(session),
                    "turn_id": remember_reply(entry, result.reply, reply_language), **extra}, cookie)


@app.post("/api/customer/chat")
async def customer_chat(body: ChatRequest, request: Request):
    return rate_limited(request, "chat") or await run_turn(request, body.text.strip())


@app.post("/api/customer/voice")
async def customer_voice(request: Request, audio: UploadFile = File(...), language: str = Form("auto")):
    """A recorded message: Whisper on the CPU, spoken account numbers normalised, then the normal turn."""
    if refused := rate_limited(request, "chat"):
        return refused
    raw = await audio.read()
    if len(raw) > config.MAX_AUDIO_BYTES:
        return JSONResponse({"error_code": "too_long", "message": "The recording is too long."}, status_code=413)
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    started = time.perf_counter()
    try:
        tmp.write(raw)
        tmp.close()
        # The customer can fix the language in the UI; "auto" lets Whisper choose between English and Bulgarian.
        text, spoken_language = await run_in_threadpool(transcribe_audio, tmp.name, language)
    except AudioTooLong as exc:
        log.info("Voice message refused: %s", exc)
        return JSONResponse({"error_code": "too_long", "message": f"The recording is too long. Please keep it under "
                             f"{config.MAX_AUDIO_SECONDS} seconds, or type your message."}, status_code=413)
    except Exception:
        log.exception("Speech recognition failed")
        return JSONResponse({"error_code": "stt_failed", "message": "Speech recognition failed. Please try again "
                             "or type your message."}, status_code=500)
    finally:
        os.remove(tmp.name)
    stt_ms = int((time.perf_counter() - started) * 1000)
    text = text.strip()[:config.MAX_MESSAGE_CHARS]
    if len(text) < 2:
        cookie, entry = get_session(request)
        return respond({"reply": NOT_UNDERSTOOD[spoken_language], "tool_calls": [], "user_text": "",
                        "turn_id": remember_reply(entry, NOT_UNDERSTOOD[spoken_language], spoken_language),
                        "spoken_language": spoken_language, "state": public_state(entry["session"])}, cookie)
    # The transcript goes back to the UI so the customer sees what was heard (in their own chat only); it is
    # redacted before it is stored or sent to the model again, like typed text.
    return await run_turn(request, text, channel="voice", stt_ms=stt_ms,
                          extra={"user_text": text, "spoken_language": spoken_language})


tts_provider = make_tts()


@app.post("/api/tts/{turn_id}")
async def text_to_speech(turn_id: str, request: Request, gender: str = Form(config.DEFAULT_VOICE_GENDER)):
    """Speak one of this session's own replies, by its turn id. Never free text: with a paid voice provider an open
    endpoint would let anyone spend its credits. Each reply is synthesised once per voice and then served from the
    session's cache."""
    if refused := rate_limited(request, "chat"):
        return refused
    entry = sessions.get(request.cookies.get(SESSION_COOKIE))
    turn = entry["replies"].get(turn_id) if entry else None
    if turn is None:
        return JSONResponse({"error_code": "unknown_turn", "message": "Nothing to speak."}, status_code=404)
    if gender in turn["audio"]:
        return Response(content=turn["audio"][gender], media_type="audio/mpeg")
    spoken = clean_for_speech(turn["text"])
    if not spoken:
        return JSONResponse({"error_code": "empty", "message": "Nothing to speak."}, status_code=400)
    language = turn["language"] if turn["language"] in config.VOICE_LANGUAGES else "en"
    audio, problem = await tts_provider.synthesize(spoken, language, gender)
    if audio:
        turn["audio"][gender] = audio
        return Response(content=audio, media_type="audio/mpeg")
    log.warning(problem)
    return JSONResponse({"error_code": "tts_failed", "message": "Voice reply is not available right now."},
                        status_code=502)


@app.post("/api/customer/reset")
def customer_reset(request: Request):
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie not in sessions:            # nothing to reset; the first message makes the session
        return JSONResponse({"status": "ok", "state": blank_state()})
    if sessions[cookie]["lock"].locked():
        return respond({"error_code": "busy", "message": "Please wait for the previous reply."}, cookie, 409)
    sessions[cookie] = _new_entry()
    return respond({"status": "ok", "state": public_state(sessions[cookie]["session"])}, cookie)


@app.get("/api/customer/state")
def customer_state(request: Request):
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie not in sessions:
        return JSONResponse({"state": blank_state()})
    sessions.move_to_end(cookie)
    return respond({"state": public_state(sessions[cookie]["session"])}, cookie)


@app.get("/api/demo-customers")
def demo_customers():
    """Fictional demo accounts for the presenter (the UI shows them; nothing is filled in automatically). They
    include the PINs, so only with DEMO_MODE=1."""
    if not config.DEMO_MODE:
        raise HTTPException(404, {"error_code": "not_found", "message": "Demo accounts are not shown here."})
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
    return {"llm": llm, "model": active, "switching": model_state["switching"],
            "embedder": {"kind": config.EMBEDDER, "model": config.EMBED_MODEL}, "kb": dict(kb_state),
            "crm": {"backend": config.CRM_BACKEND}, "tts": {"provider": config.TTS_PROVIDER}}


@app.get("/api/models")
def models():
    return {"models": list_available_models(), "active": model_state["active"],
            "switching": model_state["switching"], "target": model_state["target"]}


@app.post("/api/model")
def select_model(request: Request, model: str = Form(...)):
    # Sync endpoint: FastAPI runs it in a worker thread, so the long LM Studio load does not block the server.
    # Staff only: a switch unloads the model every conversation is using.
    staff_entry(request)
    if model not in {model_id for model_id, _ in config.ALLOWED_MODELS}:
        return JSONResponse({"error_code": "unknown_model", "message": f"Unknown model '{model}'."}, status_code=400)
    if model_state["switching"] or active_turns:
        return JSONResponse({"error_code": "busy", "message": "Try again in a moment."}, status_code=409)
    result = switch_model(model)
    return JSONResponse(result, status_code=200 if result["status"] == "success" else 500)


# --- staff: operator console and helpdesk chat (demo authentication with a token per role) ----------------------

STAFF_COOKIE = "linecare_staff"
# cookie id -> {"staff": StaffSession, "busy": threading.Lock}; one chat turn or assist job at a time per session.
staff_sessions: "OrderedDict[str, dict]" = OrderedDict()


class StaffLogin(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)


class StaffChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=config.MAX_MESSAGE_CHARS)


class CloseRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=2000)


def _new_staff(role: str, cookie: str) -> dict:
    staff = StaffSession(conv_id=db.new_conversation("helpdesk", model_state["active"]),
                         staff_id=f"{role}-{cookie[:6]}", role=role)
    return {"staff": staff, "busy": threading.Lock()}


def staff_entry(request: Request, role: str | None = None) -> dict:
    entry = staff_sessions.get(request.cookies.get(STAFF_COOKIE))
    if entry is None:
        raise HTTPException(401, {"error_code": "login_required", "message": "Sign in with a staff token."})
    if role and entry["staff"].role != role:
        raise HTTPException(403, {"error_code": "forbidden", "message": f"This needs the {role} role."})
    return entry


def staff_reply(payload: dict, status_code: int = 200, cookie: str = None) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    if cookie:
        response.set_cookie(STAFF_COOKIE, cookie, httponly=True, samesite="lax")
    return response


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"error_code": "error", "message": str(exc.detail)}
    return JSONResponse(detail, status_code=exc.status_code)


@app.post("/api/staff/login")
def staff_login(body: StaffLogin, request: Request):
    if refused := rate_limited(request, "login"):
        return refused
    role = role_for_token(body.token)
    if role is None:
        return staff_reply({"error_code": "bad_token", "message": "Unknown staff token."}, 401)
    cookie = uuid.uuid4().hex
    staff_sessions[cookie] = _new_staff(role, cookie)
    while len(staff_sessions) > config.MAX_SESSIONS:
        staff_sessions.popitem(last=False)
    staff = staff_sessions[cookie]["staff"]
    return staff_reply({"role": role, "staff_id": staff.staff_id}, cookie=cookie)


@app.get("/api/staff/me")
def staff_me(request: Request):
    staff = staff_entry(request)["staff"]
    return {"role": staff.role, "staff_id": staff.staff_id, "handover_id": staff.handover_id}


@app.post("/api/staff/logout")
def staff_logout(request: Request):
    staff_sessions.pop(request.cookies.get(STAFF_COOKIE), None)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(STAFF_COOKIE)
    return response


@app.get("/api/operator/handovers")
def operator_handovers(request: Request, status: str | None = None):
    staff_entry(request, OPERATOR)
    if status not in (None, "queued", "claimed", "closed"):
        return staff_reply({"error_code": "bad_status", "message": "status: queued, claimed or closed"}, 400)
    return {"handovers": handover_queue.list_handovers(status)}


def _assist_job(entry: dict, handover_id: str, model: str) -> None:
    global active_turns
    card = None
    with entry["busy"]:
        active_turns += 1
        try:
            card = run_assist(entry["staff"], handover_id, model)
        except Exception:
            log.exception("Assist for %s failed", handover_id)
        finally:
            active_turns -= 1
    handover_queue.assist_finished(handover_id, card)


@app.post("/api/operator/handovers/{handover_id}/claim")
def operator_claim(handover_id: str, request: Request):
    entry = staff_entry(request, OPERATOR)
    if entry["busy"].locked():
        return staff_reply({"error_code": "busy", "message": "Wait for the current reply first."}, 409)
    try:
        handover_queue.claim(handover_id, entry["staff"].staff_id)
    except handover_queue.QueueError as e:
        return staff_reply({"error_code": e.code, "message": e.message}, 404 if e.code == "not_found" else 409)
    entry["staff"].handover_id = handover_id
    db.audit(entry["staff"].staff_id, OPERATOR, "claim_handover", None, handover_id)
    handover_queue.assist_started(handover_id)
    # The claim returns at once; the assist (10-30 s on Gemma) runs in the background and the UI polls for it.
    threading.Thread(target=_assist_job, args=(entry, handover_id, model_state["active"]), daemon=True).start()
    return {"handover_id": handover_id, "status": "claimed", "assist": "pending"}


@app.get("/api/operator/handovers/{handover_id}/assist")
def operator_assist(handover_id: str, request: Request):
    staff_entry(request, OPERATOR)
    assist = handover_queue.get_assist(handover_id)
    if assist is None:
        return staff_reply({"error_code": "not_found", "message": "No assist for this handover."}, 404)
    return assist


@app.post("/api/operator/handovers/{handover_id}/close")
def operator_close(handover_id: str, body: CloseRequest, request: Request):
    entry = staff_entry(request, OPERATOR)
    try:
        result = handover_queue.close(handover_id, entry["staff"], body.note)
    except handover_queue.QueueError as e:
        return staff_reply({"error_code": e.code, "message": e.message}, 404 if e.code == "not_found" else 409)
    except Exception:
        log.exception("Closing %s failed", handover_id)
        return staff_reply({"error_code": "crm_unavailable", "message": "The CRM is not reachable; try again."}, 503)
    if entry["staff"].handover_id == handover_id:
        entry["staff"].handover_id = None
    return result


def ui_staff_call(call: dict) -> dict:
    entry = {"name": call["name"], "status": call["status"], "summary": call["summary"]}
    if call["name"] == "search_kb" and call["status"] == "ok":
        entry["sources"] = [{"doc_id": p["doc_id"], "section": p["section"], "title": p["title"],
                             "audience": p["audience"], "text": PASSAGE_WRAPPER.sub("", p["text"])}
                            for p in call["result"]["passages"]]
    return entry


@app.post("/api/helpdesk/chat")
def helpdesk_chat(body: StaffChatRequest, request: Request):
    global active_turns
    entry = staff_entry(request)
    if refused := rate_limited(request, "chat"):
        return refused
    if model_state["switching"]:
        return staff_reply({"error_code": "switching", "message": "The model is switching; try again in a minute."},
                           409)
    if not entry["busy"].acquire(blocking=False):
        return staff_reply({"error_code": "busy", "message": "Wait for the current reply (or the assist) first."},
                           409)
    active_turns += 1
    try:
        result = run_helpdesk_turn(entry["staff"], body.text.strip(), model_state["active"])
    except (APIConnectionError, APITimeoutError):
        log.error("LM Studio is not reachable at %s", config.LLM_BASE_URL)
        return staff_reply({"error_code": "llm_unavailable", "message": "The AI model is not reachable."}, 503)
    except Exception:
        log.exception("Helpdesk turn failed")
        return staff_reply({"error_code": "internal", "message": "Something went wrong; try again."}, 500)
    finally:
        active_turns -= 1
        entry["busy"].release()
    return {"reply": result.reply, "tool_calls": [ui_staff_call(c) for c in result.tool_calls],
            "model": model_state["active"], "role": entry["staff"].role}


@app.post("/api/helpdesk/reset")
def helpdesk_reset(request: Request):
    cookie = request.cookies.get(STAFF_COOKIE)
    entry = staff_entry(request)
    if entry["busy"].locked():
        return staff_reply({"error_code": "busy", "message": "Wait for the current reply first."}, 409)
    staff_sessions[cookie] = _new_staff(entry["staff"].role, cookie)
    return {"status": "ok"}


DATE = r"^\d{4}-\d{2}-\d{2}$"


@app.get("/api/metrics")
def kpis(request: Request, date_from: str | None = Query(None, alias="from", pattern=DATE),
         date_to: str | None = Query(None, alias="to", pattern=DATE),
         model: str | None = Query(None, max_length=80)):
    """KPIs of the customer conversations (ARCHITECTURE.md §12.3), for staff. Aggregates and redacted ids only."""
    staff_entry(request)
    return metrics.compute(date_from, date_to, model or None)


@app.get("/api/conversations/{conv_id}")
def conversation(conv_id: str, request: Request):
    """Transcript and tool calls of a conversation (stored redacted), for staff."""
    staff = staff_entry(request)["staff"]
    record = db.conversation_log(conv_id)
    if record["conversation"] is None:
        return staff_reply({"error_code": "not_found", "message": "No such conversation."}, 404)
    db.audit(staff.staff_id, staff.role, "read_conversation", record["conversation"]["account_no"], conv_id)
    return record
