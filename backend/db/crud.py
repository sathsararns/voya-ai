import json
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from db.models import ChatHistory, Conversation, User


def create_conversation(
    db, session_id: str, title: Optional[str] = None, user_id: Optional[str] = None
) -> Conversation:
    conversation = Conversation(
        conversation_id=str(uuid4()),
        session_id=session_id,
        user_id=user_id,
        title=title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db, conversation_id: str) -> Optional[Conversation]:
    return db.query(Conversation).filter(Conversation.conversation_id == conversation_id).first()


def get_or_create_conversation(
    db, session_id: str, conversation_id: Optional[str] = None, user_id: Optional[str] = None
) -> Conversation:
    """Look up an existing conversation, or create one.

    Used by the chat endpoint so an old/direct API caller that doesn't send
    a conversation_id still works instead of failing outright.

    `user_id` (None for an anonymous/unauthenticated caller) must match the
    existing row's owner exactly, including None == None for two anonymous
    callers — a `conversation_id` that belongs to someone else (or to an
    anonymous session when the caller is logged in, or vice versa) is never
    reused. That would either leak another account's conversation into this
    caller's chat, or silently move an anonymous conversation into a user's
    history; instead a fresh conversation is created, exactly as if no
    conversation_id had been passed at all.
    """
    if conversation_id:
        existing = get_conversation(db, conversation_id)
        if existing and existing.user_id == user_id:
            return existing

    conversation = Conversation(
        conversation_id=str(uuid4()),
        session_id=session_id,
        user_id=user_id,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(
    db, session_id: Optional[str] = None, user_id: Optional[str] = None, limit: int = 50
) -> List[Conversation]:
    """Conversations belonging to the current caller that have at least one
    message.

    A logged-in caller (`user_id` set) sees only conversations owned by
    their account, regardless of session_id — this is what keeps one
    account's Recent Chats from ever showing another account's chats in the
    same browser (session_id is a per-browser value in localStorage that
    persists across login/logout, so it must never be the scoping key for
    an authenticated caller). An anonymous caller (`user_id` is None) keeps
    the original session_id-scoped behavior, and never sees any user's
    owned conversations even if a session_id happened to match.

    A conversation row can exist with zero messages only in an edge case
    (e.g. the request failed between creating it and saving the first
    message) — filtering here keeps those from ever surfacing in the
    sidebar as empty "New conversation" placeholders.
    """
    has_message = db.query(ChatHistory.conversation_id).filter(
        ChatHistory.conversation_id.isnot(None)
    )
    query = db.query(Conversation).filter(Conversation.conversation_id.in_(has_message))

    if user_id is not None:
        query = query.filter(Conversation.user_id == user_id)
    else:
        query = query.filter(Conversation.session_id == session_id, Conversation.user_id.is_(None))

    return query.order_by(Conversation.updated_at.desc()).limit(limit).all()


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


# --- users (authentication) --------------------------------------------------


def create_user(db, email: str, name: str, password_hash: str) -> User:
    user = User(
        id=str(uuid4()),
        email=email.lower(),
        name=name,
        password_hash=password_hash,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email.lower()).first()


def get_user_by_id(db, user_id: str) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def update_user_password(db, user_id: str, password_hash: str) -> None:
    db.query(User).filter(User.id == user_id).update(
        {"password_hash": password_hash, "password_changed_at": datetime.utcnow()}
    )
    db.commit()
