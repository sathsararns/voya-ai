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

DEFAULT_NAMESPACE = "__default__"


def save_memory(memory_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    record = {
        "_id": memory_id,
        "text": text,
        "created_at": datetime.utcnow().isoformat(),
    }
    if metadata:
        record.update(metadata)

    index.upsert_records(
        namespace=DEFAULT_NAMESPACE,
        records=[record],
    )


def search_memory(query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
    result = index.search(
        namespace=DEFAULT_NAMESPACE,
        query={
            "inputs": {"text": query_text},
            "top_k": top_k,
        },
        fields=["text", "memory_type", "user_message", "created_at"],
    )

    matches = []
    for hit in getattr(result.result, "hits", []):
        matches.append(
            {
                "id": getattr(hit, "id", None),
                "score": getattr(hit, "score", None),
                "fields": getattr(hit, "fields", {}),
            }
        )
    return matches


def format_memory_context(query_text: str, top_k: int = 3) -> str:
    memories = search_memory(query_text, top_k=top_k)
    if not memories:
        return ""

    lines = ["Relevant past memory:"]
    for i, mem in enumerate(memories, start=1):
        fields = mem.get("fields", {})
        text = fields.get("text", "")
        if text:
            lines.append(f"{i}. {text}")
    return "\n".join(lines)