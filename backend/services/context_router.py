"""Agentic RAG decision layer.

A lightweight, deterministic router that decides which context sources
(conversation history, Pinecone memory, both, or neither) a given user
message actually needs — *before* the app does any retrieval or calls
Groq. This is intentionally simple and rule-based, not an LLM-based agent:
a fast, free, predictable gate that keeps unnecessary DB/Pinecone round
trips off the hot path for messages that obviously don't need them (a
greeting, a one-word command), while still pulling in richer context for
genuine follow-ups and travel-planning requests.

Designed to be swapped out later for something smarter (an LLM-based
classifier, LangGraph, a real agent) without touching any of its callers —
callers only ever depend on the ContextDecision shape, not on how it's
produced. See decide_context_strategy() for the entry point.
"""

from dataclasses import dataclass, field
import re
from typing import List


@dataclass
class ContextDecision:
    """What context to gather before calling Groq, and why."""

    use_conversation_history: bool
    use_pinecone_memory: bool
    use_summarized_context: bool
    use_knowledge_base: bool
    route_reason: str
    # Tool names (see services/tools.py's TOOL_SCHEMAS) plausibly relevant to
    # this message, e.g. ["get_weather", "calculate_budget"] — empty means
    # skip the tool-calling round entirely for this message (see
    # groq_service.py's _run_tool_calling_phase). This is a MENU, not a
    # decision to actually call anything: the model still gets to choose,
    # via its own tool_choice="auto" call, whether to use any of these,
    # exactly one, or several — this just keeps that choice scoped to a
    # short, relevant list instead of always offering every tool to every
    # message (same "don't pay for a round trip nothing needs" philosophy
    # this whole module already applies to memory/history/KB).
    candidate_tools: List[str] = field(default_factory=list)


# Standalone social greetings. Replies such as "okay" and "sure" may
# accept an offer in the previous turn, so they still need history.
GREETING_KEYWORDS = {
    "hi", "hello", "hey", "yo", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "bye", "goodbye",
}

# Statements about the user's own tastes — exactly what Pinecone memory
# exists to capture and later recall.
PREFERENCE_KEYWORDS = {
    "i like", "i love", "i prefer", "i don't like", "i dislike", "i usually",
    "i always", "i never", "remember that", "i'm a", "i am a", "i tend to",
}

# Language that only makes sense in reference to something already said —
# a strong signal the current conversation's prior turns are needed.
FOLLOWUP_KEYWORDS = {
    "instead", "change", "make it", "also add", "add a", "remove", "update",
    "modify", "what about", "continue", "same trip", "that trip", "this trip",
    "day 1", "day 2", "day 3", "day 4", "day 5",
}

# Deliberately action-oriented phrases (not generic nouns like "trip" or
# "budget", which show up in preference statements too and would make this
# over-trigger) — a real request to build a plan, not just talk about one.
PLANNING_KEYWORDS = {
    "plan a", "plan my", "plan the", "plan me", "create an itinerary",
    "build an itinerary", "suggest a", "recommend a", "help me plan",
    "itinerary for",
}

# Factual, FAQ-style travel questions — grounded in the Pinecone knowledge
# base (visa rules, currency, safety, customs, and now general destination
# guide content — attractions, landmarks) rather than in the user's own past
# preferences (memory) or the current itinerary being built (history).
KNOWLEDGE_BASE_KEYWORDS = {
    "visa", "passport", "entry requirement", "customs", "currency",
    "exchange rate", "vaccination", "vaccine", "sim card", "voltage",
    "plug type", "power outlet", "emergency number", "tipping", "tip culture",
    "public holiday", "weather", "climate", "rainy season", "monsoon",
    "best time to visit", "language spoken", "time zone", "is it safe",
    "safety", "local custom", "dress code", "airport", "domestic flight",
    "health advisory", "travel insurance",
    "things to do", "what to do", "places to visit", "worth visiting",
    "attractions", "must see", "must-see", "where to visit", "landmark",
    "tourist spot", "sightseeing",
    "train", "trains", "rail", "railway", "timetable", "bus", "buses",
    "tuk tuk", "tuk tuks", "tuktuk", "mosquito", "mosquitos", "mosquitoes",
    "repellent", "temple", "museum", "fort", "waterfall", "falls",
    "food", "cuisine", "what should i wear", "opening hours", "entrance fee",
}

DESTINATIONS = {
    "sri lanka", "sri lankan", "colombo", "kandy", "galle", "mirissa",
    "bentota", "ella", "jaffna", "sigiriya", "yala", "nuwara eliya",
    "anuradhapura", "polonnaruwa", "dambulla", "trincomalee", "negombo",
    "beruwala", "aluthgama", "matara", "hikkaduwa", "bambarakanda",
}


# Keyword groups that route to a SPECIFIC tool (see services/tools.py) —
# deliberately separate from KNOWLEDGE_BASE_KEYWORDS even though some
# overlap (e.g. "weather"): a factual weather QUESTION still uses the
# knowledge base exactly as before, and now ALSO offers the model the
# get_weather tool for structured, numeric-ish data — offering a tool never
# removes the knowledge-base path, the two are independent and can both
# fire for the same message.
WEATHER_TOOL_KEYWORDS = {
    "weather", "climate", "temperature", "rain", "rainy", "monsoon",
    "forecast", "how hot", "how cold", "what to pack",
}
BUDGET_TOOL_KEYWORDS = {
    "budget", "cost", "how much", "price", "spend", "expensive", "cheap", "afford",
}
PLACES_TOOL_KEYWORDS = {
    "places to visit", "things to do", "attractions", "recommend a place",
    "worth visiting", "must see", "must-see", "sightseeing", "where to go",
    "what to see", "spots to visit",
}
ITINERARY_TOOL_KEYWORDS = {
    "itinerary", "plan a", "plan my", "plan the", "plan me", "day trip",
    "day-by-day", "schedule my trip", "build an itinerary", "create an itinerary",
}


def _contains_any(text: str, phrases: set) -> bool:
    # Whole phrases: "rain" must not match "train", nor "cost" -> "coast".
    return any(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) for phrase in phrases)


def is_options_request(user_message: str) -> bool:
    """Travel alternatives are not a request for one selected itinerary."""
    text = user_message.lower()
    return bool(re.search(
        r"\b(?:travel|trip|tour|destination|itinerary|holiday|vacation)\s+(?:options|alternatives)\b"
        r"|\b(?:give|show|list|suggest|offer)(?:\s+me)?(?:\s+some|\s+a few)?\s+options\b",
        text,
    ))


def is_preference_statement(message: str) -> bool:
    text = message.lower()
    return bool(_contains_any(text, PREFERENCE_KEYWORDS)
                and not _contains_any(text, PLANNING_KEYWORDS)
                and not re.search(r"\?|\b(?:what|which|how|where|when|why|can you|could you)\b", text))


def is_information_only_request(message: str) -> bool:
    """Facts/lists are not day-by-day plans, even inside a planning chat."""
    text = message.lower()
    if is_options_request(message) or is_preference_statement(message):
        return False
    if re.search(r"\b(?:i|we)\s+(?:want|would like)\b", text) and not re.search(
        r"\?|\b(?:know|learn|explain|requirements?|hours|fees?|visa|eta|timetable)\b", text,
    ):
        # Stating desired hotels/meals/transport is a planning constraint,
        # even when it contains the same nouns as factual travel questions.
        return False
    if _contains_any(text, PLANNING_KEYWORDS) or re.search(
        r"\b(?:create|build|give|show|revise|adjust|design|prepare)(?:\s+me)?\s+(?:an?\s+)?itinerary\b"
        r"|\b(?:change|modify|update|remove|add|reduce|increase|make)\b.{0,45}\b(?:day|trip|budget|attraction|itinerary)\b",
        text,
    ):
        return False
    return bool(_contains_any(text, KNOWLEDGE_BASE_KEYWORDS | DESTINATIONS))


def decide_context_strategy(user_message: str, conversation_message_count: int) -> ContextDecision:
    """Classify a user message and decide which context sources to fetch.

    Deterministic and rule-based on purpose (see module docstring) — no LLM
    call here. `conversation_message_count` is the number of exchanges
    already stored for the active conversation, so history is never
    requested for a conversation that doesn't have any yet.
    """
    text = user_message.strip().lower()
    word_count = len(text.split())
    has_prior_history = conversation_message_count > 0

    # Match the whole greeting: "hi" must not match "which" or "this".
    is_greeting = text.strip(" .!?,") in GREETING_KEYWORDS
    is_preference = _contains_any(text, PREFERENCE_KEYWORDS)
    preference_only = is_preference_statement(user_message)
    is_followup = _contains_any(text, FOLLOWUP_KEYWORDS)
    is_planning = _contains_any(text, PLANNING_KEYWORDS)
    is_factual_question = (
        _contains_any(text, KNOWLEDGE_BASE_KEYWORDS)
        or (_contains_any(text, DESTINATIONS) and not is_preference)
        or is_planning
    )
    # Short travel queries ("Visa requirements", "Kandy attractions") still
    # need evidence; only actual commands/choices bypass retrieval.
    is_short_command = word_count <= 2 and not is_greeting and not is_factual_question

    # --- memory: worth it for preference statements (the whole point of
    # storing them) and for fresh planning requests (recalling past
    # preferences personalizes the plan) — but not when the message is
    # really just an edit to something already in the conversation, since
    # the preferences that shaped the original plan are already there.
    use_pinecone_memory = (
        (is_preference or (is_planning and not is_followup))
        and not is_greeting
        and not is_short_command
    )

    # Recent turns are the default for an ongoing conversation. A brief
    # reply ("2", "I choose 2", "yes", "give options") can depend entirely
    # on the previous offer even without a planning/follow-up keyword.
    # build_context keeps this bounded to the existing recent-turn window.
    use_conversation_history = has_prior_history and not is_greeting

    # --- preferred shape of that history, if used: a precise follow-up
    # ("change day 3") needs the exact prior turn, not a lossy summary; a
    # broad planning request is better served by a compact summary of
    # established facts than by replaying raw JSON. Passed through to
    # history_manager.build_context() as `prefer_summary` — it lowers the
    # summarization threshold from SUMMARY_TRIGGER to MAX_CONTEXT_MESSAGES
    # rather than forcing a summary outright (see that function's docstring).
    use_summarized_context = use_conversation_history and is_planning and not is_followup

    # --- knowledge base: independent of history/memory — a factual
    # question ("do I need a visa?") can show up alongside a planning
    # request or on its own, in a brand new conversation with no prior
    # turns, so it isn't gated on has_prior_history the way history is.
    use_knowledge_base = is_factual_question and not is_greeting and not is_short_command and not preference_only

    # --- tools: same "skip it if the message obviously doesn't need it"
    # gate as everything else here. A message can trigger more than one
    # category (e.g. "plan a 3 day trip to Kandy with a budget and what to
    # pack" plausibly wants itinerary + budget + weather all at once) — each
    # independently offered, none gated on the others.
    candidate_tools: List[str] = []
    if not is_greeting and not is_short_command and not preference_only:
        if _contains_any(text, WEATHER_TOOL_KEYWORDS):
            candidate_tools.append("get_weather")
        if _contains_any(text, BUDGET_TOOL_KEYWORDS):
            candidate_tools.append("calculate_budget")
        if _contains_any(text, PLACES_TOOL_KEYWORDS):
            candidate_tools.append("search_places")
        if _contains_any(text, ITINERARY_TOOL_KEYWORDS):
            candidate_tools.append("generate_itinerary")

    if is_options_request(user_message) and "generate_itinerary" in candidate_tools:
        candidate_tools.remove("generate_itinerary")

    if use_conversation_history and is_short_command:
        route_reason = "short_reply_uses_conversation_history"
    elif is_greeting or is_short_command:
        route_reason = "greeting_or_short_command_no_retrieval"
    elif use_conversation_history and use_pinecone_memory:
        route_reason = "complex_planning_uses_history_and_memory"
    elif use_pinecone_memory and is_preference:
        route_reason = "preference_statement_uses_memory_only"
    elif use_pinecone_memory:
        route_reason = "new_planning_request_uses_memory_only"
    elif use_conversation_history:
        route_reason = "followup_uses_conversation_history_only"
    else:
        route_reason = "no_strong_signal_no_retrieval"

    if use_knowledge_base:
        route_reason += "+kb"
    if candidate_tools:
        route_reason += "+tools(" + ",".join(candidate_tools) + ")"

    return ContextDecision(
        use_conversation_history=use_conversation_history,
        use_pinecone_memory=use_pinecone_memory,
        use_summarized_context=use_summarized_context,
        use_knowledge_base=use_knowledge_base,
        route_reason=route_reason,
        candidate_tools=candidate_tools,
    )
