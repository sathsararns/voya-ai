"""Routing regressions for short replies in an ongoing conversation."""

import pytest

from services.context_router import decide_context_strategy


@pytest.mark.parametrize("message", [
    "i choose 2", "I choose option 2", "2", "option 2", "the second one",
    "yes", "okay", "sure", "give options", "tell me more",
    "Which one is cheaper?", "This one", "Make day 2 more relaxed",
])
def test_replies_keep_recent_history_even_without_planning_keywords(message):
    decision = decide_context_strategy(message, 2)

    assert decision.use_conversation_history
    assert not decision.use_summarized_context
    assert not decision.use_pinecone_memory


@pytest.mark.parametrize("message", ["i choose 2", "2", "give options", "yes"])
def test_first_turn_does_not_request_nonexistent_history(message):
    assert not decide_context_strategy(message, 0).use_conversation_history


@pytest.mark.parametrize("message", ["hi", "Hi!", "Hello", "good morning", "thanks"])
def test_standalone_greetings_still_skip_retrieval(message):
    decision = decide_context_strategy(message, 2)

    assert not decision.use_conversation_history
    assert not decision.use_pinecone_memory
    assert not decision.use_knowledge_base
    assert decision.candidate_tools == []


def test_new_planning_request_still_uses_memory_and_history():
    decision = decide_context_strategy("Plan a trip to Galle", 2)

    assert decision.use_conversation_history
    assert decision.use_summarized_context
    assert decision.use_pinecone_memory
    assert "generate_itinerary" in decision.candidate_tools


def test_weather_followup_keeps_history_and_weather_retrieval():
    decision = decide_context_strategy("What is the weather for the second option?", 2)

    assert decision.use_conversation_history
    assert decision.use_knowledge_base
    assert decision.candidate_tools == ["get_weather"]
