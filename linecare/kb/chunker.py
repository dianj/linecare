"""KB documents: front matter + `##` sections. Each section is one chunk; long sections are split by paragraphs.

A document is a Markdown file that starts with a front matter block:

    ---
    doc_id: PUB-01
    title: "No internet: step-by-step checklist"
    audience: public          # public | internal
    group: technical          # general | policy | technical
    lang: en
    version: 3
    updated: 2026-09-15
    ---
"""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# The default embedding model reads 128 tokens (about 500 characters); longer text is cut off.
MAX_CHUNK_CHARS = 600
OVERLAP_CHARS = 100
REQUIRED_FIELDS = ("doc_id", "title", "audience", "group", "lang", "version")
AUDIENCES = {"public", "internal"}


@dataclass(frozen=True)
class Document:
    doc_id: str
    title: str
    audience: str
    group: str
    lang: str
    version: str
    updated: str
    body: str
    test_only: bool = False


@dataclass(frozen=True)
class Chunk:
    chunk_id: str      # "PUB-01#3"
    doc_id: str
    title: str
    section: str       # the `##` heading
    text: str
    audience: str
    group: str
    lang: str
    version: str
    chunk_no: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.version}\n{self.section}\n{self.text}".encode()).hexdigest()[:16]

    def metadata(self) -> dict:
        return {"doc_id": self.doc_id, "title": self.title, "section": self.section, "audience": self.audience,
                "group": self.group, "lang": self.lang, "version": self.version, "chunk_no": self.chunk_no,
                "hash": self.content_hash}


def parse_document(text: str, source: str = "") -> Document:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text.lstrip("﻿"), re.S)
    if not match:
        raise ValueError(f"{source}: no front matter")
    fields = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
    missing = [f for f in REQUIRED_FIELDS if not fields.get(f)]
    if missing:
        raise ValueError(f"{source}: missing front matter fields {missing}")
    if fields["audience"] not in AUDIENCES:
        raise ValueError(f"{source}: audience must be one of {sorted(AUDIENCES)}")
    return Document(doc_id=fields["doc_id"], title=fields["title"], audience=fields["audience"],
                    group=fields["group"], lang=fields["lang"], version=fields["version"],
                    updated=fields.get("updated", ""), body=match.group(2),
                    test_only=fields.get("test_only", "").lower() == "true")


def _split_long(text: str) -> list[str]:
    """Split a long section at paragraph breaks; each piece starts with the tail of the previous one."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces, current = [], ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > MAX_CHUNK_CHARS:
            pieces.append(current)
            tail = current[-OVERLAP_CHARS:]
            current = tail[tail.find(" ") + 1:] + "\n\n" + paragraph if " " in tail else paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def chunk_document(doc: Document) -> list[Chunk]:
    sections = re.split(r"^## +(.+)$", doc.body, flags=re.M)
    # sections = [text before the first ##, heading1, body1, heading2, body2, ...]
    chunks = []
    for heading, body in zip(sections[1::2], sections[2::2], strict=True):
        body = body.strip()
        if not body:
            continue
        for piece in (_split_long(body) if len(body) > MAX_CHUNK_CHARS else [body]):
            chunks.append(Chunk(chunk_id=f"{doc.doc_id}#{len(chunks) + 1}", doc_id=doc.doc_id, title=doc.title,
                                section=heading.strip(), text=piece, audience=doc.audience, group=doc.group,
                                lang=doc.lang, version=doc.version, chunk_no=len(chunks) + 1))
    return chunks


def load_documents(kb_dir: Path, include_test_docs: bool = False) -> list[Document]:
    folders = ["public", "internal"] + (["test"] if include_test_docs else [])
    docs = []
    for folder in folders:
        for path in sorted((kb_dir / folder).glob("*.md")):
            doc = parse_document(path.read_text(encoding="utf-8"), str(path))
            if doc.audience != ("internal" if folder == "internal" else "public"):
                raise ValueError(f"{path}: audience '{doc.audience}' does not match the folder '{folder}'")
            docs.append(doc)
    ids = [d.doc_id for d in docs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate doc_id in {kb_dir}")
    return docs
