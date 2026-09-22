from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from db.session import Base


class Conversation(Base):
    __tablename__ = "conversations"

    conversation_id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id = Column(String(255), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)
    conversation_id = Column(
        String(36), ForeignKey("conversations.conversation_id"), nullable=True, index=True
    )
    user_message = Column(Text, nullable=False)
    assistant_reply = Column(Text, nullable=False)
    destination = Column(String(255), nullable=True)
    days = Column(Integer, nullable=True)
    budget_lkr = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
