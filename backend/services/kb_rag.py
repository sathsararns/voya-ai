"""Knowledge-base RAG layer: Pinecone-backed retrieval of factual travel
content (guides, FAQs, PDFs) — visas, currency, safety, customs, etc.

Kept in a namespace completely separate from pinecone_memory.py's per-session
memory namespaces, but reuses the exact same Pinecone client/index connection
(imported, not re-created) and the same integrated-embedding upsert/search
API memory already uses. Same Pinecone project and index, two disjoint data
sets — see the module docstring in pinecone_memory.py for the memory side.

document_ingest.py is the only other module that writes here; groq_service.py
is the only other module that reads from here (via format_kb_context).
"""

import re
import sys
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

from pinecone_memory import index

# A single fixed namespace for all knowledge-base content. Memory namespaces
# are always "session_<id>" or "unscoped" (see pinecone_memory.py), so this
# name can never collide with one — a KB search can never surface a user's
# personal memory, and a memory search can never surface KB documents.
KB_NAMESPACE = "knowledge_base"

# Defensive cap on how many records go into a single upsert_records call.
# Pinecone's integrated-embedding upsert embeds every record server-side in
# that same call, so very large batches risk hitting request-size/time
# limits — chunking the upload keeps big ingestion jobs (a whole folder of
# PDFs) reliable instead of failing all-or-nothing.
UPSERT_BATCH_SIZE = 90

# Fields returned from a KB search — mirrors the metadata written by
# document_ingest.py so retrieval always has a source/title to cite.
KB_SEARCH_FIELDS = ["text", "source", "title", "document_name", "chunk_index"]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())
    return slug.strip("-") or "doc"


def _record_id(document_name: str, chunk_index: Any) -> str:
    """Deterministic record id for one (document, chunk) pair.

    Re-ingesting the same file reproduces the same ids, so Pinecone's
    upsert overwrites the existing chunk in place instead of adding a
    duplicate next to it — the core of making re-ingestion safe. Falls back
    to a random id only if a chunk somehow has no document_name.
    """
    if document_name:
        return f"kb-{_slugify(document_name)}-{chunk_index}"
    return f"kb-{uuid4()}"


def upsert_chunks(chunks: List[Dict[str, Any]]) -> int:
    """Upsert a batch of knowledge-base chunks.

    Each chunk is a plain dict with at least a "text" key; "source",
    "title", "document_name", and "chunk_index" (if present) are stored as
    metadata alongside it, and "document_name" + "chunk_index" together
    determine the record's id (see _record_id) so re-running ingestion on
    the same file replaces its chunks instead of duplicating them. Returns
    the number of chunks actually upserted (empty-text chunks are skipped).
    """
    records = []
    for chunk in chunks:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        document_name = chunk.get("document_name") or ""
        chunk_index = chunk.get("chunk_index", 0)
        records.append(
            {
                "_id": _record_id(document_name, chunk_index),
                "text": text,
                "source": chunk.get("source") or "",
                "title": chunk.get("title") or "",
                "document_name": document_name,
                "chunk_index": chunk_index,
                "created_at": datetime.utcnow().isoformat(),
            }
        )

    if not records:
        return 0

    upserted = 0
    for i in range(0, len(records), UPSERT_BATCH_SIZE):
        batch = records[i : i + UPSERT_BATCH_SIZE]
        try:
            index.upsert_records(namespace=KB_NAMESPACE, records=batch)
            upserted += len(batch)
        except Exception as e:
            print(f"[kb_rag upsert_chunks error] {e}")

    return upserted


def delete_document(document_name: str) -> None:
    """Remove every chunk belonging to one ingested document (re-ingestion
    helper — avoids duplicate/stale chunks when a source file is updated).
    """
    try:
        index.delete(
            namespace=KB_NAMESPACE,
            filter={"document_name": {"$eq": document_name}},
        )
    except Exception as e:
        print(f"[kb_rag delete_document error] {e}")


def search_knowledge_base(query_text: str, top_k: int = 4) -> List[Dict[str, Any]]:
    """Raw top-k search against the knowledge-base namespace."""
    try:
        result = index.search(
            namespace=KB_NAMESPACE,
            query={
                "inputs": {"text": query_text},
                "top_k": top_k,
            },
            fields=KB_SEARCH_FIELDS,
        )
        hits = getattr(result.result, "hits", [])
        return [
            {
                "id": getattr(hit, "id", None),
                "score": getattr(hit, "score", None),
                "fields": getattr(hit, "fields", {}),
            }
            for hit in hits
        ]
    except Exception as e:
        print(f"[kb_rag search_knowledge_base error] {e}")
        return []


def format_kb_context(hits: List[Dict[str, Any]]) -> str:
    """Format already-retrieved hits into a prompt-ready block.

    Takes hits rather than a query so a caller that also needs the raw hits
    (e.g. to build UI source citations — see build_kb_sources) only pays for
    one Pinecone search, not two. Mirrors pinecone_memory.format_memory_context's
    shape so the two read consistently once both are spliced into the Groq
    system prompt, but each line cites its source title so the model can
    tell a knowledge-base fact apart from conversational memory.
    """
    if not hits:
        return ""

    lines = ["Relevant travel knowledge-base facts:"]
    for i, hit in enumerate(hits, start=1):
        fields = hit.get("fields", {})
        text = fields.get("text", "")
        title = fields.get("title") or fields.get("document_name") or "source"
        if text:
            lines.append(f"{i}. ({title}) {text}")
    return "\n".join(lines)


# How much of a chunk's text to surface as a citation preview — long enough
# to be useful, short enough to stay "compact" in the UI.
SOURCE_SNIPPET_LENGTH = 200


def build_kb_sources(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn raw search hits into compact, UI-ready source citations.

    Same hits format_kb_context consumes, reshaped into the flat
    title/document_name/chunk_index/score/snippet shape the frontend and
    schemas.chat.KBSource expect.
    """
    sources = []
    for hit in hits:
        fields = hit.get("fields", {})
        text = (fields.get("text") or "").strip()
        document_name = fields.get("document_name") or ""
        sources.append(
            {
                "title": fields.get("title") or document_name or "source",
                "document_name": document_name,
                "chunk_index": fields.get("chunk_index"),
                "score": hit.get("score"),
                "snippet": text[:SOURCE_SNIPPET_LENGTH].strip(),
            }
        )
    return sources


if __name__ == "__main__":
    # Standalone retrieval test — run any time (not just right after
    # ingesting) to check what the knowledge base actually returns for a
    # given query:
    #     python -m services.kb_rag "do I need a visa for Sri Lanka?"
    if len(sys.argv) < 2:
        print('Usage: python -m services.kb_rag "your query text"')
        sys.exit(1)

    query_text = " ".join(sys.argv[1:])
    hits = search_knowledge_base(query_text, top_k=5)

    if not hits:
        print("No matches found in the knowledge base.")
    else:
        print(f"Top {len(hits)} match(es) for {query_text!r}:\n")
        for hit in hits:
            fields = hit.get("fields", {})
            title = fields.get("title") or fields.get("document_name") or "source"
            chunk_index = fields.get("chunk_index")
            snippet = (fields.get("text") or "")[:220].replace("\n", " ")
            print(f"[{hit.get('score'):.3f}] {title} (chunk {chunk_index})\n  {snippet}...\n")
