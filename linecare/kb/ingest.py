"""Build or update the KB index: `python -m linecare.kb.ingest [--rebuild]`.

The app also syncs the index on its first search, so running this by hand is only needed to see the counts or
to start from an empty index.
"""
import argparse
import shutil
import time

from linecare import config
from linecare.kb.chunker import load_documents
from linecare.kb.embedder import make_embedder
from linecare.kb.store import KnowledgeIndex


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="delete the index folder first")
    args = parser.parse_args(argv)
    if args.rebuild and config.CHROMA_PATH.exists():
        shutil.rmtree(config.CHROMA_PATH)
    started = time.perf_counter()
    docs = load_documents(config.KB_DIR, config.KB_INCLUDE_TEST_DOCS)
    index = KnowledgeIndex(config.CHROMA_PATH, make_embedder(), config.KB_INCLUDE_TEST_DOCS)
    counts = index.sync(docs)
    print(f"{len(docs)} documents -> {index.collection.name}: {counts} in {time.perf_counter() - started:.1f} s")


if __name__ == "__main__":
    main()
