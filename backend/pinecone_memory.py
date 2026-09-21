import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pinecone import Pinecone

load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_HOST = os.getenv("PINECONE_INDEX_HOST")

if not PINECONE_API_KEY:
    raise RuntimeError("PINECONE_API_KEY is missing")

if not PINECONE_INDEX_HOST:
    raise RuntimeError("PINECONE_INDEX_HOST is missing")

pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(host=PINECONE_INDEX_HOST)

# Used only if a request somehow arrives without a session_id. The frontend
# always generates and persists one, so this should rarely be hit — it just
# keeps such requests isolated instead of falling back to one shared pool.
FALLBACK_NAMESPACE = "unscoped"


def _namespace_for_session(session_id: Optional[str]) -> str:
    if session_id and session_id.strip():
        return f"session_{session_id.strip()}"
    return FALLBACK_NAMESPACE


def save_memory(
    session_id: Optional[str],
    memory_id: str,
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not text:
        return

    try:
        record = {
            "_id": memory_id,
            "text": text,
            "created_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }
        index.upsert_records(
            namespace=_namespace_for_session(session_id),
            records=[record],
        )
    except Exception as e:
        print(f"[Pinecone save_memory error] {e}")


def search_memory(session_id: Optional[str], query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    try:
        result = index.search(
            namespace=_namespace_for_session(session_id),
            query={
                "inputs": {"text": query_text},
                "top_k": top_k,
            },
            fields=["text", "memory_type", "user_message", "created_at"],
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
        print(f"[Pinecone search_memory error] {e}")
        return []


def format_memory_context(session_id: Optional[str], query_text: str, top_k: int = 3) -> str:
    memories = search_memory(session_id, query_text, top_k=top_k)
    if not memories:
        return ""

    lines = ["Relevant past memory:"]
    for i, mem in enumerate(memories, start=1):
        text = mem.get("fields", {}).get("text", "")
        if text:
            lines.append(f"{i}. {text}")
    return "\n".join(lines)
