import json
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from schemas.chat import ChatRequest, ChatResponse, ChatHistoryItem, ChatHistoryResponse
from services.groq_service import get_groq_reply
from db.session import SessionLocal
from db.crud import save_chat, get_chat_history
from pinecone_memory import save_memory
from memory_utils import build_memory_text

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# Used only if a client calls the endpoint without a session_id at all.
FALLBACK_SESSION_ID = "anonymous-session"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _deserialize_reply(raw_reply: str) -> dict:
    """Parse a stored assistant_reply JSON string back into a dict.

    Mirrors the fallback shape used in groq_service.get_groq_reply so old or
    malformed rows still produce a valid ChatResponse instead of a 500.
    """
    try:
        return json.loads(raw_reply)
    except (json.JSONDecodeError, TypeError):
        return {
            "destination": None,
            "days": None,
            "budget_lkr": None,
            "summary": raw_reply or "",
            "itinerary": [],
            "follow_up_question": None,
        }


@router.post("/", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    session_id = request.session_id or FALLBACK_SESSION_ID

    reply_data = get_groq_reply(request.message, session_id)

    save_chat(db, request.message, reply_data, session_id)

    memory_text = build_memory_text(request.message, reply_data)

    if memory_text:
        save_memory(
            session_id=session_id,
            memory_id=str(uuid4()),
            text=memory_text,
            metadata={
                "memory_type": "preference",
                "session_id": session_id,
                "user_message": request.message,
                "assistant_reply": json.dumps(reply_data),
            },
        )

    return reply_data


@router.get("/history/{session_id}", response_model=ChatHistoryResponse)
def get_history(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    records = get_chat_history(db, session_id, limit=limit)

    messages = [
        ChatHistoryItem(
            id=record.id,
            user_message=record.user_message,
            assistant_reply=_deserialize_reply(record.assistant_reply),
            created_at=record.created_at,
        )
        for record in records
    ]

    return ChatHistoryResponse(session_id=session_id, messages=messages)