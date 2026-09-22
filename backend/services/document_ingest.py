"""Ingestion pipeline for the travel knowledge-base RAG layer.

Loads travel docs (.txt, .md, .pdf), splits them into overlapping chunks,
and upserts them into the Pinecone knowledge-base namespace via kb_rag.py.
Uses Pinecone's integrated embedding (the same upsert_records/search API
pinecone_memory.py already uses for chat memory) — there's no separate
embedding model call or extra API key to manage here.

Run directly to ingest a single file or every supported file in a directory:

    python -m services.document_ingest path/to/docs
    python -m services.document_ingest path/to/sri-lanka-visa-faq.pdf
"""

import os
import re
import sys
from typing import Any, Dict, List, Optional

from services.kb_rag import upsert_chunks

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

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


def ingest_file(path: str, source: Optional[str] = None, title: Optional[str] = None) -> int:
    """Chunk and upsert a single file. Returns the number of chunks ingested."""
    chunks = build_chunks(path, source=source, title=title)
    if not chunks:
        return 0
    return upsert_chunks(chunks)


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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m services.document_ingest <file_or_directory>")
        sys.exit(1)

    target = sys.argv[1]
    count = ingest_path(target)
    print(f"Ingested {count} chunk(s) from {target}")
