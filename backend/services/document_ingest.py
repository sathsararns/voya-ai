"""Ingestion pipeline for the travel knowledge-base RAG layer.

Loads travel docs (.txt, .md, .pdf) from backend/docs/, splits them into
overlapping chunks, and upserts them into the Pinecone knowledge-base
namespace via kb_rag.py. Uses Pinecone's integrated embedding (the same
upsert_records/search API pinecone_memory.py already uses for chat memory)
— there's no separate embedding model call or extra API key to manage here.

Run from the backend/ folder to ingest docs/ (the default) or any other
file/directory:

    python -m services.document_ingest
    python -m services.document_ingest docs
    python -m services.document_ingest docs/visa-rules.pdf
    python -m services.document_ingest docs --query "do I need a visa?"
"""

import argparse
import os
import re
import sys
from typing import Any, Dict, List, Optional

from services.kb_rag import delete_document, search_knowledge_base, upsert_chunks

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

# Default target when no path is given — matches the docs/ folder created
# under backend/, so the common case is just `python -m services.document_ingest`.
DEFAULT_DOCS_DIR = "docs"

# Character-based (not token-based) chunk size/overlap, to avoid needing a
# tokenizer dependency — Pinecone's integrated embedding model handles the
# actual tokenization server-side. ~1200 chars is a few short paragraphs,
# comfortably inside typical embedding input limits while still small
# enough that a retrieved chunk stays focused on one topic.
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150


def _read_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError(
            "pypdf is required to ingest PDFs — install it with `pip install pypdf`"
        ) from e

    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def load_document_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext in (".txt", ".md"):
        return _read_txt(path)
    raise ValueError(f"Unsupported file type: {ext} (supported: {sorted(SUPPORTED_EXTENSIONS)})")


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks, preferring paragraph boundaries
    so a chunk doesn't cut a sentence in half mid-word. A paragraph longer
    than chunk_size on its own is hard-split as a fallback.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(para) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            step = max(chunk_size - overlap, 1)
            for i in range(0, len(para), step):
                chunks.append(para[i : i + chunk_size])
            continue

        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{para}" if tail else para

    if current:
        chunks.append(current)

    return chunks


def build_chunks(
    path: str, source: Optional[str] = None, title: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Load one file and turn it into knowledge-base chunk records, each
    carrying the metadata kb_rag.upsert_chunks expects: source, title,
    chunk_index, document_name.
    """
    document_name = os.path.basename(path)
    text = load_document_text(path)
    pieces = chunk_text(text)

    return [
        {
            "text": piece,
            "document_name": document_name,
            "title": title or document_name,
            "source": source or path,
            "chunk_index": i,
        }
        for i, piece in enumerate(pieces)
    ]


def ingest_file(
    path: str,
    source: Optional[str] = None,
    title: Optional[str] = None,
    replace: bool = True,
) -> int:
    """Chunk and upsert a single file. Returns the number of chunks ingested.

    replace=True (the default) deletes any existing chunks for this
    document_name first, so re-running ingestion on the same file is safe —
    it replaces the old chunks instead of piling up duplicates, and also
    clears out stale trailing chunks if the document got shorter since the
    last ingest (chunk ids alone can't catch that case; a same-named chunk
    would just be overwritten, but a chunk that no longer exists wouldn't
    be removed without this).
    """
    document_name = os.path.basename(path)
    chunks = build_chunks(path, source=source, title=title)

    if replace:
        delete_document(document_name)

    if not chunks:
        print(
            f"[document_ingest] WARNING: no extractable text in {document_name} - "
            "likely a scanned/image-only PDF (OCR is not implemented here). Skipped."
        )
        return 0

    upserted = upsert_chunks(chunks)
    print(f"[document_ingest] {document_name}: {upserted} chunk(s) upserted")
    return upserted


def ingest_path(path: str, source: Optional[str] = None) -> int:
    """Chunk and upsert a file, or every supported file in a directory
    (non-recursive). Returns the total number of chunks ingested.
    """
    if os.path.isdir(path):
        total = 0
        for name in sorted(os.listdir(path)):
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                total += ingest_file(os.path.join(path, name), source=source)
        return total

    return ingest_file(path, source=source)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest travel documents (.pdf, .md, .txt) into the Pinecone knowledge-base namespace.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=DEFAULT_DOCS_DIR,
        help=f"File or directory to ingest (default: {DEFAULT_DOCS_DIR})",
    )
    parser.add_argument(
        "--query",
        help="After ingesting, run a test retrieval search with this query and print the top matches.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])

    if not os.path.exists(args.path):
        print(f"Path not found: {args.path}")
        sys.exit(1)

    count = ingest_path(args.path)
    print(f"\nIngested {count} chunk(s) total from {args.path}")

    if args.query:
        print(f"\nTest retrieval for: {args.query!r}")
        hits = search_knowledge_base(args.query, top_k=4)
        if not hits:
            print("  No matches found.")
        for hit in hits:
            fields = hit.get("fields", {})
            title = fields.get("title") or fields.get("document_name") or "source"
            snippet = (fields.get("text") or "")[:160].replace("\n", " ")
            print(f"  [{hit.get('score'):.3f}] ({title}) {snippet}...")
