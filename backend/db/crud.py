import json
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from db.models import ChatHistory, Conversation


def create_conversation(db, session_id: str, title: Optional[str] = None) -> Conversation:
    conversation = Conversation(
        conversation_id=str(uuid4()),
        session_id=session_id,
        title=title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_or_create_conversation(
    db, session_id: str, conversation_id: Optional[str] = None
) -> Conversation:
    """Look up an existing conversation, or create one.

    Used by the chat endpoint so an old/direct API caller that doesn't send
    a conversation_id still works instead of failing outright.
    """
    if conversation_id:
        existing = (
            db.query(Conversation)
            .filter(Conversation.conversation_id == conversation_id)
            .first()
        )
        if existing:
            return existing

    conversation = Conversation(
        conversation_id=conversation_id or str(uuid4()),
        session_id=session_id,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(db, session_id: str, limit: int = 50) -> List[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.session_id == session_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )


def touch_conversation(db, conversation_id: str, title: Optional[str] = None) -> None:
    """Bump updated_at (for sidebar ordering) and set a title on first message."""
    conversation = (
        db.query(Conversation).filter(Conversation.conversation_id == conversation_id).first()
    )
    if not conversation:
        return

    conversation.updated_at = datetime.utcnow()
    if title and not conversation.title:
        conversation.title = title

    db.commit()


def save_chat(
    db,
    user_message: str,
    assistant_reply: dict,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> ChatHistory:
    record = ChatHistory(
        session_id=session_id,
        conversation_id=conversation_id,
        user_message=user_message,
        assistant_reply=json.dumps(assistant_reply),
        destination=assistant_reply.get("destination"),
        days=assistant_reply.get("days"),
        budget_lkr=assistant_reply.get("budget_lkr"),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_conversation_messages(db, conversation_id: str, limit: int = 100) -> List[ChatHistory]:
    return (
        db.query(ChatHistory)
        .filter(ChatHistory.conversation_id == conversation_id)
        .order_by(ChatHistory.created_at.asc())
        .limit(limit)
        .all()
    )


def count_conversation_messages(db, conversation_id: str) -> int:
    return (
        db.query(ChatHistory)
        .filter(ChatHistory.conversation_id == conversation_id)
        .count()
    )


def get_recent_messages(db, conversation_id: str, limit: int = 6) -> List[ChatHistory]:
    """Most recent `limit` exchanges for a conversation, oldest first.

    Used to build LLM context, so it fetches from the newest end (unlike
    get_conversation_messages, which is for paging through display history
    from the start) and reverses back to chronological order before returning.
    """
    records = (
        db.query(ChatHistory)
        .filter(ChatHistory.conversation_id == conversation_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(records))
