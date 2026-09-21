from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from db.session import Base

class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)
    user_message = Column(Text, nullable=False)
    assistant_reply = Column(Text, nullable=False)
    destination = Column(String(255), nullable=True)
    days = Column(Integer, nullable=True)
    budget_lkr = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)