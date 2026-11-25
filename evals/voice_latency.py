"""Voice turn latency per stage (review T18.C1): what a customer waits for between the end of speaking and the
first sound of the reply.

20 first-turn utterances (10 English, 10 Bulgarian) are synthesised once with edge-tts as the customer's voice,
then each goes through the real pipeline on a fresh seeded database: faster-whisper on the CPU (STT), the customer
agent with the real model in LM Studio (LLM, all tool rounds), and the configured voice provider for the reply
(TTS). There is no streaming yet, so time to first audio = STT + LLM + TTS. Also counted: whether the spoken account
number came out of STT as the right `QN-` number.

    python evals/voice_latency.py [--model gemma-4-26b-a4b-it] [--digits whole|spelled] [--output voice-latency]

`--digits spelled` reads account numbers and PINs digit by digit ("Q N 1 0 0 0 0 1"), as callers usually say
them; `whole` lets the voice read "100001" as a number ("one hundred thousand and one").

Writes evals/results/<output>.md and a JSON file with every utterance.
"""
import argparse
import asyncio
import json
import math
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="linecare-voice-")
os.environ["DB_PATH"] = os.path.join(_TMP, "voice.db")
os.environ["FROZEN_NOW"] = "2026-11-20T10:00:00"
os.environ["CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ.setdefault("STAFF_TOKENS", "unused:operator")

from linecare import config, db, lmstudio  # noqa: E402
from linecare.agents.customer import run_customer_turn  # noqa: E402
from linecare.kb import retriever  # noqa: E402
from linecare.session import Session  # noqa: E402
from linecare.voice import tts  # noqa: E402
from linecare.voice.stt import get_whisper_model, transcribe_audio  # noqa: E402

RESULTS_DIR = os.path.join(ROOT, "evals", "results")

# (language, account the utterance names, what the customer says). Accounts and PINs are the fictional demo ones.
UTTERANCES = [
    ("en", "QN-100001", "Hi, my internet stopped working this morning. My account number is QN 100001 and my PIN is "
                        "4821."),
    ("en", "QN-100002", "Hello. The internet keeps dropping every few minutes. Account QN 100002, PIN 7305."),
    ("en", "QN-100004", "Good morning. My Wi-Fi is very slow in the evenings. It's account QN 100004, PIN 2468."),
    ("en", "QN-100006", "Why is my internet blocked? My account is QN 100006 and the PIN is 6042."),
    ("en", "QN-100007", "I want to check my last bill. Account number QN 100007, PIN 3716."),
    ("en", None, "Is there an internet outage in Sofia right now?"),
    ("en", None, "How do I change the name of my Wi-Fi network?"),
    ("en", None, "What do I need to do when I move house?"),
    ("en", None, "Can I talk to a person, please?"),
    ("en", None, "My TV box shows a black screen. What can I do?"),
    ("bg", "QN-100003", "Здравейте, интернетът ми не работи от сутринта. Номерът ми е QN 100003, ПИН 1590."),
    ("bg", "QN-100001", "Добър ден. Нямам интернет. Акаунт QN 100001, ПИН 4821."),
    ("bg", "QN-100002", "Интернетът прекъсва постоянно. Номер на акаунта QN 100002, ПИН 7305."),
    ("bg", "QN-100004", "Безжичната мрежа е много бавна вечер. Акаунт QN 100004, ПИН 2468."),
    ("bg", "QN-100007", "Искам да проверя последната си сметка. QN 100007, ПИН 3716."),
    ("bg", None, "Има ли авария с интернета в София в момента?"),
    ("bg", None, "Как да сменя името на безжичната мрежа?"),
    ("bg", None, "Какво трябва да направя, когато се местя?"),
    ("bg", None, "Искам да говоря с човек, моля."),
    ("bg", None, "Телевизията ми показва черен екран. Какво да направя?"),
]
CUSTOMER_VOICE = {"en": "male", "bg": "male"}   # the reply uses the app's default voice; the customer another one


def spell_digits(text: str) -> str:
    """'QN 100001 ... PIN 4821' -> 'Q N 1 0 0 0 0 1 ... PIN 4 8 2 1': the way a caller reads an id aloud."""
    text = re.sub(r"\bQN\b", "Q N", text)
    return re.sub(r"\d{4,}", lambda m: " ".join(m.group(0)), text)


def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)) - 1, 0)] if ordered else None


async def synthesise_customer(text, language, path):
    audio, problem = await tts.EdgeTTS().synthesize(text, language, CUSTOMER_VOICE[language])
    if not audio:
        raise RuntimeError(f"edge-tts failed: {problem}")
    with open(path, "wb") as f:
        f.write(audio)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=config.ALLOWED_MODELS[0][0])
    parser.add_argument("--digits", choices=("whole", "spelled"), default="spelled")
    parser.add_argument("--output", default="voice-latency")
    args = parser.parse_args()

    db.ensure_database()
    template = os.path.join(_TMP, "template.db")
    shutil.copyfile(config.DB_PATH, template)
    retriever.get_index()
    get_whisper_model()                        # loaded once before timing, as in the running app after its first call
    if lmstudio.switch_model(args.model)["status"] != "success":
        sys.exit(f"could not load {args.model}")
    reply_tts = tts.make_tts()

    rows = []
    for i, (language, account, text) in enumerate(UTTERANCES):
        clip = os.path.join(_TMP, f"u{i:02d}.mp3")
        spoken = spell_digits(text) if args.digits == "spelled" else text
        asyncio.run(synthesise_customer(spoken, language, clip))
        shutil.copyfile(template, config.DB_PATH)
        started = time.perf_counter()
        heard, heard_language = transcribe_audio(clip, "auto")
        stt_ms = int((time.perf_counter() - started) * 1000)
        session = Session(conv_id=db.new_conversation("customer", args.model))
        started = time.perf_counter()
        result = run_customer_turn(session, heard, args.model, channel="voice", stt_ms=stt_ms)
        llm_ms = int((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        audio, problem = asyncio.run(reply_tts.synthesize(result.reply, heard_language, config.DEFAULT_VOICE_GENDER))
        tts_ms = int((time.perf_counter() - started) * 1000)
        account_ok = None if account is None else account in re.findall(r"QN-\d{6}", heard)
        rows.append({"language": language, "said": text, "heard": heard, "heard_language": heard_language,
                     "account_ok": account_ok, "stt_ms": stt_ms, "llm_ms": llm_ms, "tts_ms": tts_ms,
                     "first_audio_ms": stt_ms + llm_ms + tts_ms, "tts_ok": bool(audio), "tts_problem": problem,
                     "tools": [c["name"] for c in result.tool_calls], "reply": result.reply})
        print(f"{language} stt {stt_ms:5d}  llm {llm_ms:6d}  tts {tts_ms:5d}  account_ok={account_ok}  "
              f"heard={heard!r}", flush=True)
    lmstudio.switch_model(config.MODEL_NAME)

    stamp = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, f"{args.output}-{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    lines = [f"# Voice turn latency ({stamp})", "",
             f"{len(rows)} first-turn utterances (10 English, 10 Bulgarian), synthesised once with edge-tts as the "
             f"customer; model {args.model} in LM Studio; STT faster-whisper {config.WHISPER_MODEL} on the CPU "
             f"(int8); reply voice {config.TTS_PROVIDER}; account numbers and PINs read {args.digits} "
             f"({'digit by digit' if args.digits == 'spelled' else 'as whole numbers'}). No streaming: time to first "
             "audio = STT + LLM + TTS. "
             "Generated by `python evals/voice_latency.py`.", "",
             "| Language | n | STT median / p95 | LLM median / p95 | TTS median / p95 | First audio median / p95 | "
             "Account number heard right |", "|---|---|---|---|---|---|---|"]
    for language in ("en", "bg", None):
        part = [r for r in rows if language is None or r["language"] == language]
        accounts = [r["account_ok"] for r in part if r["account_ok"] is not None]
        cells = [f"{statistics.median(r[k] for r in part) / 1000:.1f} / "
                 f"{percentile([r[k] for r in part], 95) / 1000:.1f} s"
                 for k in ("stt_ms", "llm_ms", "tts_ms", "first_audio_ms")]
        lines.append(f"| {language or 'all'} | {len(part)} | " + " | ".join(cells) +
                     f" | {sum(accounts)}/{len(accounts)} |")
    failed = [r for r in rows if not r["tts_ok"]]
    if failed:
        lines += ["", f"Voice output failed for {len(failed)} replies: " + "; ".join(r["tts_problem"] for r in failed)]
    misheard = [r for r in rows if r["account_ok"] is False]
    if misheard:
        lines += ["", "Account numbers not heard right:", ""] + [f"- said {r['said']!r} → heard {r['heard']!r}"
                                                                 for r in misheard]
    with open(os.path.join(RESULTS_DIR, f"{args.output}.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
