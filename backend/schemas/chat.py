from datetime import datetime

from pydantic import BaseModel, Field
from typing import List, Optional


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ItineraryDay(BaseModel):
    day: int
    title: str
    items: List[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    destination: Optional[str] = None
    days: Optional[int] = None
    budget_lkr: Optional[int] = None
    summary: str
    itinerary: List[ItineraryDay] = Field(default_factory=list)
    follow_up_question: Optional[str] = None


class ChatHistoryItem(BaseModel):
    id: int
    user_message: str
    assistant_reply: ChatResponse
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    session_id: str
    messages: List[ChatHistoryItem] = Field(default_factory=list)