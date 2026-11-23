"""Search the knowledge base with access control in code (spec §8.3).

The caller fixes which audiences may be searched; the customer tool always passes {"public"} and the model has no
parameter to widen it. Chroma filters on `audience`, and every passage is checked again in Python before it is
returned (defence in depth): one passage with a wrong audience means no passages at all, and an error in the log.
"""
import logging
import threading
from dataclasses import dataclass

from linecare import config
from linecare.kb.chunker import load_documents
from linecare.kb.embedder import make_embedder
from linecare.kb.store import KnowledgeIndex

log = logging.getLogger("linecare.kb")


class AccessViolation(Exception):
    """A passage outside the allowed audiences reached the result."""


@dataclass(frozen=True)
class Passage:
    doc_id: str
    title: str
    section: str
    text: str
    audience: str
    group: str
    lang: str
    score: float


@dataclass(frozen=True)
class SearchResult:
    passages: list
    answerable: bool
    top_score: float


_index = None
_lock = threading.Lock()


def get_index() -> KnowledgeIndex:
    """The index for the configured embedder, synced with the files on first use (the startup check of §8.2)."""
    global _index
    with _lock:
        if _index is None:
            index = KnowledgeIndex(config.CHROMA_PATH, make_embedder(), config.KB_INCLUDE_TEST_DOCS)
            index.sync(load_documents(config.KB_DIR, config.KB_INCLUDE_TEST_DOCS))
            _index = index
        return _index


def set_index(index) -> None:
    """Use another index (tests); None makes the next search build it from the configuration again."""
    global _index
    _index = index


def _where(audiences: frozenset, groups: frozenset | None) -> dict:
    clauses = [{"audience": {"$in": sorted(audiences)}}]
    if groups:
        clauses.append({"group": {"$in": sorted(groups)}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def search(query: str, *, audiences: frozenset, groups: frozenset | None = None, k: int = None,
           index=None) -> SearchResult:
    if not audiences:
        raise ValueError("audiences must name at least one audience")
    index = index or get_index()
    rows = index.query(query, _where(frozenset(audiences), groups), k or config.KB_TOP_K)
    try:
        for row in rows:
            if row["audience"] not in audiences or (groups and row["group"] not in groups):
                raise AccessViolation(f"{row['doc_id']} ({row['audience']}/{row['group']}) for {sorted(audiences)}")
    except AccessViolation:
        log.exception("KB access violation; no passages returned")
        return SearchResult(passages=[], answerable=False, top_score=0.0)
    passages = [Passage(doc_id=r["doc_id"], title=r["title"], section=r["section"], text=r["text"],
                        audience=r["audience"], group=r["group"], lang=r["lang"], score=r["score"]) for r in rows]
    top = passages[0].score if passages else 0.0
    threshold = config.KB_MIN_SCORE if config.KB_MIN_SCORE is not None else index.embedder.min_score
    if top < threshold:
        return SearchResult(passages=[], answerable=False, top_score=top)
    # Keep only passages close to the best one, so weak neighbours do not dilute the answer.
    return SearchResult(passages=[p for p in passages if p.score >= threshold], answerable=True, top_score=top)
