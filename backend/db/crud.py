import json
from typing import Optional

from db.models import ChatHistory

def save_chat(db, user_message: str, assistant_reply: dict, session_id: Optional[str] = None):
    record = ChatHistory(
        session_id=session_id,
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