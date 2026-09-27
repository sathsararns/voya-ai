"""Persistent, account-scoped travel-preferences summary.

Distinct from pinecone_memory.py's conversational memory: that's free-text,
semantic-search retrieval scoped by session_id (a per-browser value that
deliberately survives login/logout — see frontend/src/hooks/useAppStore.ts's
getOrCreateSessionId), which makes it a poor fit for "remember this specific
ACCOUNT's travel style" — a session_id can outlive one account and be reused
by another in the same browser. This module instead maintains a small,
structured dict stored directly on the User row (db/models.py's
User.preferences), keyed by nothing but the account's own primary key, so
there is no shared namespace for two accounts to ever collide in — one
user's preferences physically cannot appear on another user's row.

Deliberately deterministic/keyword-based, not an LLM call — same
lightweight-extraction philosophy this project already applies elsewhere
(see context_router.py's own docstring, and history_manager.py's
_summarize_messages, whose HOTEL_KEYWORDS/TRANSPORT_KEYWORDS/FOOD_KEYWORDS
this module reuses directly rather than redefining them, so "budget hotel"
means the same thing to both the per-conversation summary and the
cross-session preferences memory).

Only ever invoked for a resolved, authenticated User — routes/chat.py never
calls extract_and_merge_preferences for an anonymous caller, so anonymous
chat never reads or writes this at all, and never gets attached to an
account after the fact.
"""

from typing import Any, Dict, List, Optional

from services.history_manager import FOOD_KEYWORDS, HOTEL_KEYWORDS, TRANSPORT_KEYWORDS

# Not covered by history_manager.py's existing keyword maps (those are about
# accommodation/transport/food specifically) — activity interests and
# overall trip pacing/style are their own dimensions.
ACTIVITY_KEYWORDS: Dict[str, str] = {
    "hiking": "Hiking", "trekking": "Hiking", "climbing": "Hiking",
    "wildlife": "Wildlife", "safari": "Wildlife", "national park": "Wildlife",
    "beach": "Beach", "surfing": "Beach", "snorkel": "Beach", "diving": "Beach",
    "temple": "Cultural sightseeing", "cultural": "Cultural sightseeing",
    "heritage": "Cultural sightseeing", "museum": "Cultural sightseeing",
    "shopping": "Shopping", "nightlife": "Nightlife", "photography": "Photography",
    "spa": "Relaxation", "wellness": "Relaxation",
}

TRAVEL_STYLE_KEYWORDS: Dict[str, str] = {
    "fast-paced": "Fast-paced", "fast paced": "Fast-paced", "packed schedule": "Fast-paced",
    "relaxed": "Relaxed", "slow travel": "Relaxed", "laid-back": "Relaxed", "chill trip": "Relaxed",
    "luxury": "Luxury", "honeymoon": "Honeymoon", "backpack": "Backpacking",
    "family": "Family", "kids": "Family", "with children": "Family",
    "solo": "Solo", "solo travel": "Solo", "by myself": "Solo",
    "budget travel": "Budget", "budget trip": "Budget", "adventure": "Adventure",
}

# Bounds on the list-shaped fields — this is meant to stay a small,
# glanceable summary, not an ever-growing log of every message a user has
# ever sent. Most-recently-mentioned first (see _push_distinct), so an
# old, never-repeated preference naturally ages out instead of growing the
# list forever.
MAX_LIST_ITEMS = 5


def _find_keyword(text: str, keyword_map: Dict[str, str]) -> Optional[str]:
    lower = text.lower()
    for keyword, label in keyword_map.items():
        if keyword in lower:
            return label
    return None


def _push_distinct(existing: List[str], new_value: Optional[str], limit: int = MAX_LIST_ITEMS) -> List[str]:
    if not new_value:
        return existing
    result = [v for v in existing if v != new_value]
    result.insert(0, new_value)
    return result[:limit]


def extract_and_merge_preferences(
    existing: Optional[Dict[str, Any]], user_message: str, reply_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Pure function: given a user's current stored preferences (or None for
    a first-time user) plus one turn's user message + validated reply,
    returns the UPDATED preferences dict. Never mutates `existing` in place
    (the caller compares old vs. new to decide whether a write is even
    needed — see routes/chat.py) and never raises: a malformed `reply_data`
    (missing keys, unexpected types) is treated as "nothing new to learn
    from that field" rather than erroring the whole chat turn over a
    preferences side-effect.

    Structured facts (destination/days/budget) come straight from the
    model's own validated JSON output — already normalized and validated by
    the time this runs (see groq_service.py) — the same source
    history_manager.py's own summarizer already trusts for the same fields.
    Everything else is a keyword scan of the user's raw message text.
    """
    prefs: Dict[str, Any] = dict(existing or {})
    prefs.setdefault("preferred_destinations", [])
    prefs.setdefault("food_preferences", [])
    prefs.setdefault("activity_preferences", [])

    destination = reply_data.get("destination")
    if isinstance(destination, str) and destination.strip():
        prefs["preferred_destinations"] = _push_distinct(prefs["preferred_destinations"], destination.strip())

    days = reply_data.get("days")
    if isinstance(days, int) and not isinstance(days, bool):
        prefs["typical_trip_duration_days"] = days

    budget_lkr = reply_data.get("budget_lkr")
    if isinstance(budget_lkr, int) and not isinstance(budget_lkr, bool):
        current_min = prefs.get("budget_lkr_min")
        current_max = prefs.get("budget_lkr_max")
        prefs["budget_lkr_min"] = budget_lkr if current_min is None else min(current_min, budget_lkr)
        prefs["budget_lkr_max"] = budget_lkr if current_max is None else max(current_max, budget_lkr)

    hotel = _find_keyword(user_message, HOTEL_KEYWORDS)
    if hotel:
        prefs["hotel_style"] = hotel

    transport = _find_keyword(user_message, TRANSPORT_KEYWORDS)
    if transport:
        prefs["transport_preference"] = transport

    food = _find_keyword(user_message, FOOD_KEYWORDS)
    if food:
        prefs["food_preferences"] = _push_distinct(prefs["food_preferences"], food)

    activity = _find_keyword(user_message, ACTIVITY_KEYWORDS)
    if activity:
        prefs["activity_preferences"] = _push_distinct(prefs["activity_preferences"], activity)

    travel_style = _find_keyword(user_message, TRAVEL_STYLE_KEYWORDS)
    if travel_style:
        prefs["travel_style"] = travel_style

    return prefs


def format_preferences_context(preferences: Optional[Dict[str, Any]]) -> str:
    """Turns a stored preferences dict into a short prompt-ready block — same
    shape/spirit as pinecone_memory.format_memory_context and
    services/kb_rag.format_kb_context, spliced into the system prompt
    alongside them (see groq_service.py's _build_prompt_messages). Returns
    "" for an empty/None preferences dict, so a first-time user's prompt is
    completely unaffected by this feature existing at all.
    """
    if not preferences:
        return ""

    lines: List[str] = []

    destinations = preferences.get("preferred_destinations") or []
    if destinations:
        lines.append(f"Has previously asked about: {', '.join(destinations)}.")

    budget_min = preferences.get("budget_lkr_min")
    budget_max = preferences.get("budget_lkr_max")
    if isinstance(budget_min, int) and isinstance(budget_max, int):
        if budget_min == budget_max:
            lines.append(f"Usual budget: around {budget_min:,} LKR.")
        else:
            lines.append(f"Usual budget range: {budget_min:,}-{budget_max:,} LKR.")

    duration = preferences.get("typical_trip_duration_days")
    if isinstance(duration, int):
        lines.append(f"Typically plans {duration}-day trips.")

    if preferences.get("hotel_style"):
        lines.append(f"Hotel preference: {preferences['hotel_style']}.")
    if preferences.get("transport_preference"):
        lines.append(f"Transport preference: {preferences['transport_preference']}.")
    if preferences.get("food_preferences"):
        lines.append(f"Food preferences: {', '.join(preferences['food_preferences'])}.")
    if preferences.get("activity_preferences"):
        lines.append(f"Enjoys: {', '.join(preferences['activity_preferences'])}.")
    if preferences.get("travel_style"):
        lines.append(f"Overall travel style: {preferences['travel_style']}.")

    if not lines:
        return ""

    return (
        "Known preferences for this returning user (personalize suggestions "
        "accordingly, but don't force these into every reply if irrelevant "
        "to the current question):\n" + "\n".join(lines)
    )
