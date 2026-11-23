"""Text embeddings: ONNX models on the CPU (fastembed, no torch) and a deterministic fake for tests and CI.

The CPU keeps the GPU's memory free for the 26-27B chat model. Each model has its own calibrated relevance
threshold (`min_score`) for the `answerable` rule, measured with evals/retrieval_eval.py (spec §8.3).
"""
import hashlib
import math
import re
from typing import Protocol

from linecare import config

# Cosine similarity a top passage needs for the question to count as answerable, per model (calibrated in D2,
# see evals/results/retrieval.md). Models with an e5 prefix need "query: " / "passage: " in front of the text.
# The default (first) model won on hit rate, separation and size; the others stay selectable via EMBED_MODEL.
MODELS = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": {"min_score": 0.46, "prefixes": ("", "")},
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": {"min_score": 0.446, "prefixes": ("", "")},
    "intfloat/multilingual-e5-large": {"min_score": 0.807, "prefixes": ("query: ", "passage: ")},
}


class Embedder(Protocol):
    name: str
    min_score: float

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OnnxEmbedder:
    def __init__(self, model: str = None, cache_dir=None):
        self.name = model or config.EMBED_MODEL
        settings = MODELS.get(self.name, {"min_score": 0.5, "prefixes": ("", "")})
        self.min_score = settings["min_score"]
        self.query_prefix, self.passage_prefix = settings["prefixes"]
        self._cache_dir = str(cache_dir or config.EMBED_CACHE_DIR)
        self._model = None

    def _load(self):
        if self._model is None:  # lazy: loading takes a few seconds and is not needed until the first search
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.name, cache_dir=self._cache_dir)
        return self._model

    def embed_passages(self, texts):
        return [v.tolist() for v in self._load().embed([self.passage_prefix + t for t in texts])]

    def embed_query(self, text):
        return next(iter(self._load().embed([self.query_prefix + text]))).tolist()


WORD = re.compile(r"\w+", re.U)


class FakeEmbedder:
    """Hashed bag of words: texts that share words score high. Enough to test filters and wiring, not quality."""

    name = "fake-hashing-256"
    min_score = 0.3
    dims = 256

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        for word in WORD.findall(text.lower()):
            if len(word) > 2:
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_passages(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


def make_embedder(kind: str = None) -> Embedder:
    kind = kind or config.EMBEDDER
    if kind == "fake":
        return FakeEmbedder()
    if kind == "onnx":
        return OnnxEmbedder()
    raise ValueError(f"Unknown EMBEDDER '{kind}' (use onnx or fake)")
