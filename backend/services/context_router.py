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

from dataclasses import dataclass


@dataclass
class ContextDecision:
    """What context to gather before calling Groq, and why."""

    use_conversation_history: bool
    use_pinecone_memory: bool
    use_summarized_context: bool
    route_reason: str


# Short greetings/acknowledgements — never worth any retrieval.
GREETING_KEYWORDS = {
    "hi", "hello", "hey", "yo", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "ok", "okay", "sure", "bye", "goodbye", "cool", "nice",
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


def _contains_any(text: str, phrases: set) -> bool:
    return any(phrase in text for phrase in phrases)


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

    is_greeting = word_count <= 4 and _contains_any(text, GREETING_KEYWORDS)
    is_short_command = word_count <= 2 and not is_greeting
    is_preference = _contains_any(text, PREFERENCE_KEYWORDS)
    is_followup = _contains_any(text, FOLLOWUP_KEYWORDS)
    is_planning = _contains_any(text, PLANNING_KEYWORDS)

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

    # --- conversation history: only if the conversation actually has prior
    # turns, and only if this message plausibly builds on them (a follow-up
    # edit, or a planning request substantial enough to likely reference
    # earlier context).
    use_conversation_history = (
        has_prior_history
        and (is_followup or is_planning)
        and not is_greeting
        and not is_short_command
    )

    # --- preferred shape of that history, if used: a precise follow-up
    # ("change day 3") needs the exact prior turn, not a lossy summary; a
    # broad planning request is better served by a compact summary of
    # established facts than by replaying raw JSON. Passed through to
    # history_manager.build_context() as `prefer_summary` — it lowers the
    # summarization threshold from SUMMARY_TRIGGER to MAX_CONTEXT_MESSAGES
    # rather than forcing a summary outright (see that function's docstring).
    use_summarized_context = use_conversation_history and is_planning and not is_followup

    if is_greeting or is_short_command:
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

    return ContextDecision(
        use_conversation_history=use_conversation_history,
        use_pinecone_memory=use_pinecone_memory,
        use_summarized_context=use_summarized_context,
        route_reason=route_reason,
    )
