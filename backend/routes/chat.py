import json
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatHistoryItem,
    ChatHistoryResponse,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationListResponse,
)
from services.groq_service import get_groq_reply
from services.history_manager import build_context, to_groq_messages
from services.context_router import decide_context_strategy
from db.models import User
from db.session import SessionLocal
from db.crud import (
    save_chat,
    get_conversation,
    get_conversation_messages,
    count_conversation_messages,
    create_conversation,
    get_or_create_conversation,
    list_conversations,
    touch_conversation,
)
from pinecone_memory import save_memory
from memory_utils import build_memory_text
from routes.auth import get_current_user_optional

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# Used only if a client calls an endpoint without a session_id at all.
FALLBACK_SESSION_ID = "anonymous-session"

CONVERSATION_TITLE_MAX_LENGTH = 60


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
            "kb_sources": [],
        }


def _default_title(user_message: str) -> str:
    text = user_message.strip()
    if len(text) > CONVERSATION_TITLE_MAX_LENGTH:
        return f"{text[:CONVERSATION_TITLE_MAX_LENGTH]}…"
    return text


@router.post("/", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    session_id = request.session_id or FALLBACK_SESSION_ID
    user_id = current_user.id if current_user else None
    conversation = get_or_create_conversation(db, session_id, request.conversation_id, user_id=user_id)
    conversation_id = conversation.conversation_id

    # Agentic RAG decision layer: figure out which context sources this
    # message actually needs BEFORE touching the DB/Pinecone for them, so a
    # greeting or a one-word command skips retrieval entirely instead of
    # paying for it and throwing the result away.
    message_count = count_conversation_messages(db, conversation_id)
    decision = decide_context_strategy(request.message, message_count)
    print(
        f"[context_router] reason={decision.route_reason} "
        f"history={decision.use_conversation_history} "
        f"memory={decision.use_pinecone_memory} "
        f"summarized={decision.use_summarized_context} "
        f"knowledge_base={decision.use_knowledge_base}"
    )

    history = []
    if decision.use_conversation_history:
        context = build_context(
            db, conversation_id, prefer_summary=decision.use_summarized_context
        )
        history = to_groq_messages(context)

    reply_data = get_groq_reply(
        request.message,
        session_id,
        history=history,
        use_memory=decision.use_pinecone_memory,
        use_knowledge_base=decision.use_knowledge_base,
    )

    save_chat(db, request.message, reply_data, session_id, conversation_id)
    touch_conversation(db, conversation_id, title=_default_title(request.message))

    memory_text = build_memory_text(request.message, reply_data)

    if memory_text:
        save_memory(
            session_id=session_id,
            memory_id=str(uuid4()),
            text=memory_text,
            metadata={
                "memory_type": "preference",
                "session_id": session_id,
                "conversation_id": conversation_id,
                "user_message": request.message,
                "assistant_reply": json.dumps(reply_data),
            },
        )

    reply_data["conversation_id"] = conversation_id
    return reply_data


@router.post("/conversations", response_model=ConversationResponse)
def create_new_conversation(
    request: ConversationCreateRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    session_id = request.session_id or FALLBACK_SESSION_ID
    user_id = current_user.id if current_user else None
    conversation = create_conversation(db, session_id, request.title, user_id=user_id)
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        session_id=conversation.session_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/conversations", response_model=ConversationListResponse)
def get_conversations(
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # A logged-in caller is scoped by account (session_id, if sent, is
    # ignored — see list_conversations) so switching accounts in the same
    # browser only ever shows that account's own chats, and the frontend no
    # longer sends session_id for this call at all once authenticated (see
    # useAppStore.ts's apiListConversations). An anonymous caller still
    # needs a session_id to scope by, same as before auth existed — but
    # with neither a session cookie nor a session_id, there is simply no
    # identity to list conversations for. That's not a malformed request
    # (every field the client sent, or omitted, was valid on its own), so
    # it isn't a 400 — it's "nothing to show yet", the same empty result a
    # real caller with zero conversations would get. This also means a
    # brief window where the session cookie hasn't landed yet (e.g. right
    # after login, before it's committed) degrades to an empty list rather
    # than a hard error.
    if current_user is None and not session_id:
        return ConversationListResponse(conversations=[])

    conversations = list_conversations(
        db, session_id=session_id, user_id=current_user.id if current_user else None, limit=limit
    )
    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                conversation_id=c.conversation_id,
                session_id=c.session_id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in conversations
        ]
    )


@router.get("/conversations/{conversation_id}/messages", response_model=ChatHistoryResponse)
def get_conversation_history(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # Same ownership rule as get_or_create_conversation: a conversation's
    # owner (None for anonymous) must match the caller exactly. Without
    # this, any caller who learned a conversation_id — by guessing a UUID
    # is infeasible, but e.g. a shared link, browser history, or a bug
    # elsewhere — could read another account's messages by id alone.
    conversation = get_conversation(db, conversation_id)
    caller_id = current_user.id if current_user else None
    if conversation is None or conversation.user_id != caller_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    records = get_conversation_messages(db, conversation_id, limit=limit)

    messages = [
        ChatHistoryItem(
            id=record.id,
            user_message=record.user_message,
            assistant_reply=_deserialize_reply(record.assistant_reply),
            created_at=record.created_at,
        )
        for record in records
    ]

    return ChatHistoryResponse(conversation_id=conversation_id, messages=messages)
