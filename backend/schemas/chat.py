from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


def _as_utc(value: datetime) -> datetime:
    """DB timestamps are stored naive but always represent UTC
    (models use datetime.utcnow()). Attach explicit UTC tzinfo before this
    gets serialized to JSON, so the ISO string carries a timezone offset —
    without it, the frontend's `new Date(...)` misreads the value as local
    time instead of UTC, shifting the displayed time by the user's offset.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None


class ItineraryDay(BaseModel):
    day: int
    title: str
    items: List[str] = Field(default_factory=list)


class KBSource(BaseModel):
    """One knowledge-base chunk that grounded an answer — see
    services/kb_rag.py build_kb_sources(), which is what actually produces
    these. Kept separate from ChatHistoryItem's memory data; this only ever
    describes knowledge_base-namespace hits, never Pinecone chat memory.
    """

    title: str
    document_name: str
    chunk_index: Optional[int] = None
    score: Optional[float] = None
    snippet: str


class ChatResponse(BaseModel):
    destination: Optional[str] = None
    days: Optional[int] = None
    budget_lkr: Optional[int] = None
    summary: str
    itinerary: List[ItineraryDay] = Field(default_factory=list)
    follow_up_question: Optional[str] = None
    conversation_id: Optional[str] = None
    kb_sources: List[KBSource] = Field(default_factory=list)


class ChatHistoryItem(BaseModel):
    id: int
    user_message: str
    assistant_reply: ChatResponse
    created_at: datetime

    _normalize_created_at = field_validator("created_at", mode="before")(_as_utc)


class ChatHistoryResponse(BaseModel):
    conversation_id: str
    messages: List[ChatHistoryItem] = Field(default_factory=list)


class ConversationCreateRequest(BaseModel):
    session_id: Optional[str] = None
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    conversation_id: str
    session_id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    _normalize_timestamps = field_validator("created_at", "updated_at", mode="before")(_as_utc)


class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse] = Field(default_factory=list)
