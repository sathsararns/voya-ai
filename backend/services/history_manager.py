"""Conversation history optimization for the Groq prompt.

Long conversations shouldn't be replayed in full on every request — this
mirrors how ChatGPT-style assistants keep prompts bounded: recent turns are
sent to the model verbatim, older turns are compressed into a short
deterministic summary that preserves the travel-planning facts that
actually matter (destination, budget, days, preferences, what's already
been decided), instead of their full raw text.

This module owns that decision end to end: loading history, counting it,
deciding whether to summarize, building the summary, and shaping the final
list of Groq/OpenAI-style messages. routes/chat.py only ever calls
build_context() + to_groq_messages() — it has no history/summarization
logic of its own.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from db.crud import count_conversation_messages, get_conversation_messages, get_recent_messages
from db.models import ChatHistory

# How many of the most recent exchanges are always sent to Groq verbatim,
# whether or not summarization is active.
MAX_CONTEXT_MESSAGES = 8

# Total stored exchanges a conversation needs to reach before its older
# messages get summarized instead of sent raw. Below this, the whole
# conversation is still short enough that MAX_CONTEXT_MESSAGES alone keeps
# the prompt small — there's nothing "old" worth compressing yet.
SUMMARY_TRIGGER = 20


@dataclass
class ConversationContext:
    """Optimized context ready to hand to the Groq prompt builder."""

    summary: Optional[str]
    recent_messages: List[ChatHistory]
    total_messages: int
    summarized: bool


# --- deterministic summarization --------------------------------------------

HOTEL_KEYWORDS = {
    "budget hotel": "Budget hotel",
    "luxury hotel": "Luxury hotel",
    "resort": "Resort",
    "hostel": "Hostel",
    "guesthouse": "Guesthouse",
    "homestay": "Homestay",
}

TRANSPORT_KEYWORDS = {
    "tuk tuk": "Tuk tuk",
    "tuktuk": "Tuk tuk",
    "rental car": "Rental car",
    "train": "Train",
    "car": "Car",
    "bus": "Bus",
    "taxi": "Taxi",
    "flight": "Flight",
}

FOOD_KEYWORDS = {
    "vegetarian": "Vegetarian",
    "vegan": "Vegan",
    "non-vegetarian": "Non-vegetarian",
    "seafood": "Seafood",
    "halal": "Halal",
}


def _find_keyword(text: str, keyword_map: Dict[str, str]) -> Optional[str]:
    lower = text.lower()
    for keyword, label in keyword_map.items():
        if keyword in lower:
            return label
    return None


def _summarize_messages(records: List[ChatHistory]) -> str:
    """Deterministically compress older exchanges into a compact summary.

    Structured facts (destination/days/budget) are read straight from the
    already-denormalized ChatHistory columns, with the last non-null value
    winning — a later message ("change it to 5 days") should override an
    earlier one, not be averaged or listed twice. Planned stops come from
    each turn's structured itinerary (already clean JSON by the time it's
    stored — see json_utils.normalize_chat_response), not free-text
    guessing. Preferences (hotel/transport/food) are picked up from the
    user's own wording via a small keyword lookup, the same lightweight
    approach memory_utils.py already uses for Pinecone memory extraction.
    """
    destination: Optional[str] = None
    days: Optional[int] = None
    budget_lkr: Optional[int] = None
    hotel: Optional[str] = None
    transport: Optional[str] = None
    food: Optional[str] = None
    planned: List[str] = []
    seen_planned = set()

    for record in records:
        if record.destination:
            destination = record.destination
        if record.days is not None:
            days = record.days
        if record.budget_lkr is not None:
            budget_lkr = record.budget_lkr

        hotel = _find_keyword(record.user_message, HOTEL_KEYWORDS) or hotel
        transport = _find_keyword(record.user_message, TRANSPORT_KEYWORDS) or transport
        food = _find_keyword(record.user_message, FOOD_KEYWORDS) or food

        try:
            reply = json.loads(record.assistant_reply)
        except (json.JSONDecodeError, TypeError):
            reply = {}

        for day in reply.get("itinerary") or []:
            title = (day or {}).get("title")
            if title and title not in seen_planned:
                seen_planned.add(title)
                planned.append(title)

    lines = ["Previous conversation summary:", ""]
    lines.append(f"Destination: {destination or 'Not specified'}")
    lines.append(f"Budget: {f'{budget_lkr} LKR' if budget_lkr else 'Not specified'}")
    lines.append(f"Days: {days if days is not None else 'Not specified'}")

    if hotel:
        lines += ["", "Hotel preference:", hotel]

    if transport:
        lines += ["", "Transport:", transport]

    if food:
        lines += ["", "Food:", food]

    if planned:
        lines += ["", "Already planned:", *planned]

    return "\n".join(lines)


# --- public entry points -----------------------------------------------------


def build_context(db: Session, conversation_id: str) -> ConversationContext:
    """Load a conversation's history, trimming/summarizing it if it's grown long."""
    total = count_conversation_messages(db, conversation_id)

    if total <= SUMMARY_TRIGGER:
        recent = get_recent_messages(db, conversation_id, limit=MAX_CONTEXT_MESSAGES)
        return ConversationContext(
            summary=None,
            recent_messages=recent,
            total_messages=total,
            summarized=False,
        )

    all_messages = get_conversation_messages(db, conversation_id, limit=total)
    older_messages = all_messages[:-MAX_CONTEXT_MESSAGES]
    recent_messages = all_messages[-MAX_CONTEXT_MESSAGES:]

    return ConversationContext(
        summary=_summarize_messages(older_messages),
        recent_messages=recent_messages,
        total_messages=total,
        summarized=True,
    )


def to_groq_messages(context: ConversationContext) -> List[Dict[str, str]]:
    """Turn an optimized context into Groq/OpenAI-style prior turns.

    Shape: [summary (if any)] + [recent turns, oldest first]. The caller
    still appends the current user message on top of this.
    """
    messages: List[Dict[str, str]] = []

    if context.summary:
        messages.append(
            {
                "role": "system",
                "content": f"{context.summary}\n\nCurrent recent conversation:",
            }
        )

    for record in context.recent_messages:
        messages.append({"role": "user", "content": record.user_message})
        messages.append({"role": "assistant", "content": record.assistant_reply})

    return messages
