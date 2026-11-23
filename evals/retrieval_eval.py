"""Retrieval quality per embedding model: hit rate and the `answerable` threshold (ARCHITECTURE.md §8.3, §8.4).

    python evals/retrieval_eval.py [--models m1,m2]

For every model: build a fresh index of the public docs, run the questions below, and report hit@1 and hit@4
(is an expected document among the top passages), the top scores of answerable vs unanswerable questions, and
the threshold that separates them best. Writes evals/results/retrieval.md. No LLM is needed.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from linecare import config  # noqa: E402
from linecare.kb import retriever  # noqa: E402
from linecare.kb.chunker import load_documents  # noqa: E402
from linecare.kb.embedder import MODELS, OnnxEmbedder  # noqa: E402
from linecare.kb.store import KnowledgeIndex  # noqa: E402

NO_INTERNET = {"PUB-01", "PUB-14-BG"}
# (question, documents that answer it; empty = not in the knowledge base)
QUESTIONS = [
    ("How can I pay my bill?", {"PUB-09"}),
    ("Can I pay in cash somewhere?", {"PUB-09"}),
    ("My account was suspended. When does the service come back after I pay?", {"PUB-08"}),
    ("When is my monthly bill due?", {"PUB-08"}),
    ("Can I move the technician visit to another day?", {"PUB-07"}),
    ("At what times do technicians come?", {"PUB-07"}),
    ("Do I have to pay for a technician visit?", {"PUB-07"}),
    ("Wi-Fi is slow but with a cable the speed is fine", {"PUB-02"}),
    ("Should my phone use 2.4 or 5 GHz?", {"PUB-02"}),
    ("Where is the best place for my router?", {"PUB-02"}),
    ("What does the orange ring on the QN-Box 4 mean?", {"PUB-03"}),
    ("The light on the second mesh unit is yellow", {"PUB-03"}),
    ("How do I restart the router myself?", {"PUB-04", *NO_INTERNET}),
    ("Is a factory reset the same as a restart?", {"PUB-04"}),
    ("How will you tell me about planned maintenance?", {"PUB-06"}),
    ("Will I get money back for the outage?", {"PUB-06"}),
    ("Only my laptop has no internet, the other devices work", NO_INTERNET),
    ("I have no internet at all, what should I check?", NO_INTERNET),
    ("Как мога да платя сметката си?", {"PUB-09"}),
    ("Мога ли да сменя часа за посещението на техника?", {"PUB-07"}),
    ("Само телефонът ми няма интернет, другите работят", NO_INTERNET),
    ("Wi-Fi-ят е бавен, а с кабел е добре", {"PUB-02"}),
    ("Спряха ми услугите заради неплатена сметка, какво да направя?", {"PUB-08"}),
    ("Какво означава оранжевата светлина на рутера?", {"PUB-03"}),
    ("Do you sell iPhones?", set()),
    ("What will the weather be in Sofia tomorrow?", set()),
    ("Can I get a Netflix subscription through you?", set()),
    ("How much does roaming in Greece cost on my mobile plan?", set()),
    ("Who is the CEO of Quillnet?", set()),
    ("How do I cancel my contract?", set()),
    ("Do you offer business fibre with a static IP address?", set()),
    ("Продавате ли телевизори?", set()),
    ("Колко струва мобилен план с 20 GB?", set()),
]


def best_threshold(answerable: list[float], unanswerable: list[float]) -> tuple[float, float]:
    """The cut that classifies the most questions correctly (ties: the midpoint of the widest gap)."""
    candidates = sorted(set(answerable + unanswerable))
    best = (0.0, 0.0)
    for low, high in zip(candidates, candidates[1:] + [candidates[-1] + 0.01], strict=True):
        cut = round((low + high) / 2, 3)
        correct = sum(s >= cut for s in answerable) + sum(s < cut for s in unanswerable)
        accuracy = correct / (len(answerable) + len(unanswerable))
        if accuracy > best[1]:
            best = (cut, accuracy)
    return best


def evaluate(model: str) -> dict:
    embedder = OnnxEmbedder(model)
    docs = [d for d in load_documents(config.KB_DIR) if d.audience == "public"]
    started = time.perf_counter()
    index = KnowledgeIndex(None, embedder)
    index.sync(docs)
    build_s = time.perf_counter() - started
    hits1 = hits4 = 0
    answerable_tops, unanswerable_tops, misses, query_ms = [], [], [], []
    for question, expected in QUESTIONS:
        started = time.perf_counter()
        rows = index.query(question, {"audience": {"$in": ["public"]}}, config.KB_TOP_K)
        query_ms.append((time.perf_counter() - started) * 1000)
        top = rows[0]["score"]
        if expected:
            answerable_tops.append(top)
            ids = [r["doc_id"] for r in rows]
            hits1 += ids[0] in expected
            hits4 += bool(expected & set(ids))
            if ids[0] not in expected:
                misses.append(f"{question!r}: got {ids[0]} ({top:.3f}), expected {sorted(expected)}")
        else:
            unanswerable_tops.append(top)
    cut, accuracy = best_threshold(answerable_tops, unanswerable_tops)
    n = len(answerable_tops)
    return {"model": model, "hit1": hits1 / n, "hit4": hits4 / n, "n": n, "n_unanswerable": len(unanswerable_tops),
            "answerable_min": min(answerable_tops), "answerable_median": statistics.median(answerable_tops),
            "unanswerable_max": max(unanswerable_tops),
            "unanswerable_median": statistics.median(unanswerable_tops), "threshold": cut,
            "threshold_accuracy": accuracy, "build_s": build_s, "query_ms": statistics.median(query_ms),
            "misses": misses}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(MODELS))
    args = parser.parse_args()
    retriever.set_index(None)
    results = []
    for model in args.models.split(","):
        result = evaluate(model)
        results.append(result)
        print(f"{model}: hit@1 {result['hit1']:.0%}, hit@4 {result['hit4']:.0%}, threshold {result['threshold']} "
              f"({result['threshold_accuracy']:.0%}), build {result['build_s']:.1f} s", flush=True)

    lines = ["# Retrieval: embedding models compared", "",
             f"{results[0]['n']} answerable and {results[0]['n_unanswerable']} unanswerable questions (EN and BG) "
             "against the public KB; top-4 passages; CPU (ONNX via fastembed).", "",
             "| Model | hit@1 | hit@4 | answerable top score (min / median) | unanswerable top score (median / max) "
             "| best threshold (accuracy) | index build | query |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| `{r['model']}` | {r['hit1']:.0%} | {r['hit4']:.0%} | {r['answerable_min']:.3f} / "
                     f"{r['answerable_median']:.3f} | {r['unanswerable_median']:.3f} / {r['unanswerable_max']:.3f} | "
                     f"{r['threshold']} ({r['threshold_accuracy']:.0%}) | {r['build_s']:.1f} s | "
                     f"{r['query_ms']:.0f} ms |")
    for r in results:
        if r["misses"]:
            lines += ["", f"Top-1 misses for `{r['model']}`:"] + [f"- {m}" for m in r["misses"]]
    out = ROOT / "evals" / "results" / "retrieval.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Written {out}")


if __name__ == "__main__":
    main()
