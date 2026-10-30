"""Regressions from a single mixed chat: facts, sources, then preferences."""

import json
from unittest.mock import MagicMock

import pytest

from memory_utils import build_memory_text
from services import groq_service
from services.answer_policy import information_fallback
from services.context_router import decide_context_strategy, is_information_only_request
from services.retrieval_policy import relevant_hits
from services.user_preferences import extract_and_merge_preferences


TRAIN_QUESTION = (
    "According to the bundled train timetable notice, list only Maradana to Galle "
    "departures. Include the notice date and service days. Do not include Galle to Maradana services."
)
MUSEUM_QUESTION = (
    "What are the opening hours of the Atlantis Moon Museum in Sri Lanka? "
    "Do not invent a place or opening hours."
)
PREFERENCE = "Remember that I prefer vegetarian meals and travelling by train for future trips."
HISTORY = [
    {"role": "user", "content": "Plan a 3-day trip to Kandy"},
    {"role": "assistant", "content": json.dumps({"destination": "Kandy", "days": 3,
        "budget_lkr": 45000, "summary": "A Kandy trip.",
        "itinerary": [{"day": 1, "title": "Arrival", "items": ["Visit the temple"]}]})},
]


@pytest.mark.parametrize("query", [TRAIN_QUESTION, MUSEUM_QUESTION, "Visa requirements", "Kandy attractions"])
def test_factual_questions_are_not_itinerary_requests(query):
    assert is_information_only_request(query)


@pytest.mark.parametrize("query", [
    "Plan a 2-day trip to Kandy using my saved food and transport preferences.",
    "Make the second day more relaxed and include vegetarian meals.",
    "Add one more cultural attraction without increasing my total budget.",
    "Reduce my total budget to LKR 30000. Keep the same destinations.",
    "i choose 2",
])
def test_real_plans_and_followup_edits_keep_their_itinerary_shape(query):
    assert not is_information_only_request(query)


def test_factual_train_list_is_flattened_and_does_not_inherit_trip_budget():
    data = {"summary": "The dated notice lists these departures.", "destination": "Sri Lanka",
            "days": 1, "itinerary": [{"day": 1, "title": "Maradana to Galle departures",
                                      "items": ["17:25 - Daily", "17:45 - Daily", "18:45 - Daily"]}]}
    retry = MagicMock(side_effect=AssertionError("This is a valid factual answer"))
    result = groq_service._prepare_reply(TRAIN_QUESTION, data, retry, history=HISTORY)
    assert result["itinerary"] == []
    assert "17:25" in result["summary"] and "18:45" in result["summary"]
    for field in ["destination", "days", "budget_lkr", "budget_breakdown", "fee_notice", "budget_cap_lkr"]:
        assert result.get(field) is None
    retry.assert_not_called()


def test_named_entity_must_match_even_with_no_invention_instructions():
    generic = {"score": 0.95, "fields": {"text": "Many places in Sri Lanka have opening hours. "
               "Do not invent attractions; confirm museum details with available sources."}}
    assert relevant_hits(MUSEUM_QUESTION, [generic], 4) == []
    actual = {"score": 0.8, "fields": {"text": "Atlantis Moon Museum is a fictional venue in this example."}}
    assert relevant_hits(MUSEUM_QUESTION, [actual], 4) == [actual]


def test_generic_the_temple_phrase_does_not_hide_known_tooth_temple_evidence():
    evidence = {"score": 0.8, "fields": {"text": "Kandy's Temple of the Sacred Tooth houses the tooth relic."}}
    query = "Tell me about the Temple of the Sacred Tooth in Kandy"
    assert relevant_hits(query, [evidence], 4) == [evidence]


def test_preference_acknowledgement_does_not_search_public_travel_documents(monkeypatch):
    decision = decide_context_strategy(PREFERENCE, 8)
    assert decision.use_pinecone_memory
    assert not decision.use_knowledge_base
    assert decision.candidate_tools == []
    kb = MagicMock(side_effect=AssertionError("Preference acknowledgements need no public references"))
    monkeypatch.setattr(groq_service, "search_knowledge_base", kb)
    _, sources, used = groq_service._build_prompt_messages(
        PREFERENCE, None, HISTORY, False, True, "test", candidate_tools=[])
    assert sources == [] and used == []
    kb.assert_not_called()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("query", ["Visa requirements", MUSEUM_QUESTION])
def test_unsupported_factual_answers_cannot_guess_or_reuse_previous_sources(monkeypatch, streaming, query):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setattr(groq_service, "search_knowledge_base", lambda *_a, **_k: [])
    model = MagicMock(side_effect=AssertionError("No model guess is needed without evidence"))
    monkeypatch.setattr(groq_service, "Groq", model)
    kwargs = dict(history=HISTORY, use_memory=False, candidate_tools=[])
    if streaming:
        events = list(groq_service.stream_groq_reply(query, **kwargs))
        result = next(data for event, data in events if event == "done")
    else:
        result = groq_service.get_groq_reply(query, **kwargs)
    assert result["kb_sources"] == [] and result["itinerary"] == []
    assert result.get("fee_notice") is None and result.get("destination") is None
    assert "cannot verify" in result["summary"]
    assert "30 days" not in result["summary"] and "visa on arrival" not in result["summary"]
    model.assert_not_called()


def test_static_visa_snippets_do_not_establish_current_rules():
    result = information_fallback("Visa requirements", [{"snippet": "Tourist visas last 30 days."}], "evidence_found")
    assert "30 days" not in result["summary"]
    assert "passport nationality" in result["follow_up_question"]


def test_timetable_inquiry_does_not_become_a_saved_train_preference():
    existing = {"food_preferences": ["Vegetarian"], "transport_preference": "Bus"}
    data = {"destination": "Sri Lanka", "days": 1, "itinerary": []}
    assert extract_and_merge_preferences(existing, TRAIN_QUESTION, data) == existing
    assert build_memory_text(TRAIN_QUESTION, data) == ""
    assert existing["transport_preference"] == "Bus"


def test_explicit_preferences_are_still_saved():
    data = extract_and_merge_preferences({}, PREFERENCE, {})
    assert data["transport_preference"] == "Train"
    assert data["food_preferences"] == ["Vegetarian"]
