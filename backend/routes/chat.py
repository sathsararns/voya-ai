import json
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from schemas.chat import ChatRequest, ChatResponse
from services.groq_service import get_groq_reply
from db.session import SessionLocal
from db.crud import save_chat
from pinecone_memory import save_memory
from memory_utils import build_memory_text

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    reply_data = get_groq_reply(request.message)

    save_chat(db, request.message, reply_data)

    memory_text = build_memory_text(request.message, reply_data)

    if memory_text:
        save_memory(
            memory_id=str(uuid4()),
            text=memory_text,
            metadata={
                "memory_type": "preference",
                "user_message": request.message,
                "assistant_reply": json.dumps(reply_data),
            },
        )

    return reply_data