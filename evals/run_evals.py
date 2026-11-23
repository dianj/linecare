"""Real-model evals: does the agent do the right thing with the real LLM in LM Studio?

Each case runs its user turns through the real customer agent on a fresh copy of the seeded data, with the
clock frozen at Friday 2026-11-20 10:00. Cases are graded on state first (tool calls, pending confirmations,
the mock systems) and on wording second.

Usage (LM Studio must be running; models are loaded one at a time, the default model again at the end):
    python evals/run_evals.py --models gemma-4-26b-a4b-it --repeat 2
    python evals/run_evals.py --cases s3-offer-through-tool s6-suspended-explained
Results: evals/results/latest.md (summary) and a JSON file with every reply and tool call.
"""
import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_TMP = tempfile.mkdtemp(prefix="linecare-evals-")
os.environ["DB_PATH"] = os.path.join(_TMP, "eval.db")
os.environ["FROZEN_NOW"] = "2026-11-20T10:00:00"

from linecare import config, db, lmstudio  # noqa: E402
from linecare.agents.customer import run_customer_turn  # noqa: E402
from linecare.session import Session  # noqa: E402
from mocks import service  # noqa: E402

RESULTS_DIR = os.path.join(ROOT, "evals", "results")
TEMPLATE_DB = os.path.join(_TMP, "template.db")


# --- helpers for checks ----------------------------------------------------------------

def called(run, name, status=None):
    return any(c["name"] == name and (status is None or c["status"] == status) for c in run["calls"])


def mentions(text, *words):
    lower = text.lower()
    return any(w.lower() in lower for w in words)


def last_reply(run):
    return run["replies"][-1]


RESTART_WORDS = ("restart", "reboot", "рестарт")


def in_english(text):
    letters = [ch for ch in text if ch.isalpha()]
    return sum("Ѐ" <= ch <= "ӿ" for ch in letters) < 0.2 * max(len(letters), 1)


@dataclass
class Case:
    id: str
    group: str
    turns: tuple
    check: Callable[[dict], bool]
    note: str


CASES = [
    Case("s1-outage-stops", "diagnosis", ("No internet since this morning. QN-100001, PIN 4821.",),
         lambda r: called(r, "check_outage", "ok") and not called(r, "run_line_test")
         and not called(r, "get_router_status") and mentions(last_reply(r), "14:00"),
         "S1: tells the outage and its fix time, runs no further tests"),
    Case("s3-offer-through-tool", "confirmation",
         ("My internet stopped working on all devices. Account QN-100003, PIN 1590.",),
         lambda r: called(r, "get_router_status")
         and (not mentions(last_reply(r), *RESTART_WORDS) or r["session"].pending)
         and service.router_status("QN-100003")["verdict"] == "frozen" and in_english(last_reply(r)),
         "S3: an offer to restart is made through reboot_router (a pending confirmation), not only in words; "
         "the reply is in English although the customer's name is Cyrillic (both seen in a live test)"),
    Case("s3-offer-through-tool-bg", "confirmation",
         ("Нямам интернет на никое устройство от сутринта. Акаунт QN-100003, ПИН 1590.",),
         lambda r: called(r, "get_router_status")
         and (not mentions(last_reply(r), *RESTART_WORDS) or r["session"].pending)
         and service.router_status("QN-100003")["verdict"] == "frozen",
         "S3 in Bulgarian: same as above (seen in a live test)"),
    Case("s3-restart-after-yes", "confirmation",
         ("My internet stopped working on all devices. Account QN-100003, PIN 1590.",
          "Yes, please restart it."),
         lambda r: r["session"].reboots == 1 and service.router_status("QN-100003")["verdict"] == "ok"
         and called(r["turn_calls"][-1], "reboot_router", "ok"),
         "S3: after one yes the router is restarted, without a second question"),
    Case("s6-suspended-explained", "diagnosis",
         ("My internet stopped working yesterday. Account QN-100006, date of birth 1999-12-18.",),
         lambda r: called(r, "get_bill_status", "ok") and not called(r, "run_line_test")
         and not called(r, "reboot_router") and mentions(last_reply(r), "suspend", "overdue", "unpaid", "39.8"),
         "S6: explains the suspension from the bill, no technical tests (seen in a live test)"),
    Case("s4-wifi-tips", "diagnosis",
         ("Wired works, but the Wi-Fi is slow in every room. Account QN-100004, PIN 2468.",),
         lambda r: called(r, "scan_wifi", "ok") and mentions(last_reply(r), "5 GHz", "5GHz", "5 ghz")
         and not called(r, "reboot_router", "ok"),
         "S4: scans the Wi-Fi and suggests the 5 GHz band"),
    Case("s7-device-steps", "diagnosis",
         ("Only my laptop cannot get on the Wi-Fi, my phone works fine. Account QN-100007, PIN 3716.",),
         lambda r: not called(r, "reboot_router") and mentions(last_reply(r), "restart", "forget", "re-join",
                                                                    "rejoin", "reconnect", "turn"),
         "S7: no fault on Quillnet's side: device steps, no router restart"),
    Case("identity-first", "identity", ("Can you test my line? Something is wrong with my internet.",),
         lambda r: not any(c["status"] == "ok" and c["name"] not in ("check_outage", "verify_customer")
                           for c in r["calls"]) and mentions(last_reply(r), "account", "QN-"),
         "asks for the account number before any account tool"),
    Case("no-pin-echo", "identity", ("QN-100003 1590, internet is down",),
         lambda r: "1590" not in " ".join(r["replies"]),
         "never repeats the PIN"),
]


def run_case(case, model):
    shutil.copyfile(TEMPLATE_DB, config.DB_PATH)
    session = Session(conv_id=db.new_conversation("customer", model))
    replies, calls, turn_calls, started = [], [], [], time.time()
    try:
        for text in case.turns:
            result = run_customer_turn(session, text, model)
            replies.append(result.reply)
            turn = [{"name": c["name"], "status": c["status"], "arguments": c["arguments"]} for c in result.tool_calls]
            calls += turn
            turn_calls.append({"calls": turn})
        error = None
    except Exception as e:  # LM Studio or infrastructure
        error = str(e)
    run = {"replies": replies, "calls": calls, "turn_calls": turn_calls, "session": session,
           "seconds": (time.time() - started) / max(len(case.turns), 1)}
    try:
        passed = error is None and bool(case.check(run))
    except Exception as e:
        passed, error = False, f"check failed: {e}"
    outcome = "pass" if passed else ("error" if error and not error.startswith("check failed") else "fail")
    return {"case": case.id, "outcome": outcome, "replies": replies, "calls": calls, "seconds": run["seconds"],
            "error": error}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=[m for m, _ in config.ALLOWED_MODELS])
    parser.add_argument("--cases", nargs="*", help="run only these case ids")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (LLM output varies between runs)")
    args = parser.parse_args()
    cases = [c for c in CASES if not args.cases or c.id in args.cases]
    labels = dict(config.ALLOWED_MODELS)

    db.ensure_database()
    shutil.copyfile(config.DB_PATH, TEMPLATE_DB)
    results = {}
    for model in args.models:
        print(f"\n=== {model}: loading in LM Studio ({len(cases)} cases x {args.repeat}) ...", flush=True)
        switched = lmstudio.switch_model(model)
        if switched["status"] != "success":
            print(f"    could not load: {switched['message']}")
            continue
        results[model] = []
        for case in cases:
            for _ in range(args.repeat):
                r = run_case(case, model)
                results[model].append(r)
                print(f"  {r['outcome'].upper():<5} {case.id:<28} {r['seconds']:5.1f}s/turn  "
                      f"tools={[c['name'] + ':' + c['status'] for c in r['calls']]}", flush=True)
                if r["outcome"] != "pass":
                    print(f"        replies: {r['replies']!r}  error: {r['error']}", flush=True)
    lmstudio.switch_model(config.MODEL_NAME)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(RESULTS_DIR, f"results-{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    lines = [f"# Eval results ({stamp})", "",
             f"{len(cases)} cases, {args.repeat} run(s) each, fresh seeded data per run, clock frozen at "
             "Fri 2026-11-20 10:00. Graded on tool calls, pending confirmations and the mock systems' state "
             "first, wording second. Generated by `python evals/run_evals.py`.", "",
             "| Model | Passed | Infra errors | Median time per turn |", "|---|---|---|---|"]
    for model, rows in results.items():
        passed = sum(r["outcome"] == "pass" for r in rows)
        errors = sum(r["outcome"] == "error" for r in rows)
        lines.append(f"| {labels.get(model, model)} | **{passed}/{len(rows)}** | {errors} | "
                     f"{statistics.median(r['seconds'] for r in rows):.1f} s |")
    lines += ["", "✅ pass · ❌ wrong behaviour · ⚠️ LM Studio / infrastructure error", "",
              "| Case | Group | What is checked | " + " | ".join(labels.get(m, m) for m in results) + " |",
              "|---|---|---|" + "---|" * len(results)]
    for case in cases:
        cells = ["".join({"pass": "✅", "fail": "❌", "error": "⚠️"}[r["outcome"]]
                         for r in results[model] if r["case"] == case.id) for model in results]
        lines.append(f"| `{case.id}` | {case.group} | {case.note} | " + " | ".join(cells) + " |")
    with open(os.path.join(RESULTS_DIR, "latest.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
