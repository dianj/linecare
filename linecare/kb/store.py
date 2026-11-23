"""The vector index (Chroma, on disk). One collection per embedding model, so switching models never mixes vectors.

`sync` re-embeds only chunks whose content hash changed and deletes chunks that no longer exist, so a restart
after editing one document takes a moment, not a full rebuild.
"""
import logging
import re

import chromadb
from chromadb.config import Settings

from linecare.kb.chunker import Chunk, Document, chunk_document

log = logging.getLogger("linecare.kb")


def collection_name(embedder_name: str, include_test_docs: bool) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", embedder_name).strip("-").lower()[:50]
    return f"kb-{slug}{'-test' if include_test_docs else ''}"


class KnowledgeIndex:
    def __init__(self, path, embedder, include_test_docs: bool = False):
        """`path=None` keeps the index in memory and starts it empty (tests, evals)."""
        self.embedder = embedder
        settings = Settings(anonymized_telemetry=False)
        name = collection_name(embedder.name, include_test_docs)
        if path is None:
            client = chromadb.EphemeralClient(settings=settings)
            if name in [c.name for c in client.list_collections()]:
                client.delete_collection(name)
        else:
            client = chromadb.PersistentClient(path=str(path), settings=settings)
        self.collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"},
                                                          embedding_function=None)

    def sync(self, documents: list[Document]) -> dict:
        """Bring the index in line with the documents. Returns counts of added/updated, removed, unchanged."""
        chunks: dict[str, Chunk] = {c.chunk_id: c for d in documents for c in chunk_document(d)}
        existing = self.collection.get(include=["metadatas"])
        stored = {cid: meta.get("hash") for cid, meta in zip(existing["ids"], existing["metadatas"], strict=True)}
        changed = [c for cid, c in chunks.items() if stored.get(cid) != c.content_hash]
        removed = [cid for cid in stored if cid not in chunks]
        if removed:
            self.collection.delete(ids=removed)
        if changed:
            self.collection.upsert(ids=[c.chunk_id for c in changed],
                                   embeddings=self.embedder.embed_passages([f"{c.title}. {c.section}\n{c.text}"
                                                                            for c in changed]),
                                   documents=[c.text for c in changed], metadatas=[c.metadata() for c in changed])
        counts = {"changed": len(changed), "removed": len(removed), "unchanged": len(chunks) - len(changed)}
        if changed or removed:
            log.info("KB index %s synced: %s", self.collection.name, counts)
        return counts

    def query(self, text: str, where: dict, k: int) -> list[dict]:
        """Nearest chunks matching the Chroma `where` filter, as dicts with metadata, text and cosine score."""
        result = self.collection.query(query_embeddings=[self.embedder.embed_query(text)], n_results=k,
                                       where=where, include=["documents", "metadatas", "distances"])
        return [{**meta, "text": doc, "score": round(1 - distance, 4)}
                for doc, meta, distance in zip(result["documents"][0], result["metadatas"][0],
                                               result["distances"][0], strict=True)]
