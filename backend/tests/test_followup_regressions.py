"""Exact reported multi-turn edits must retain structured itinerary metadata."""

import json
from unittest.mock import MagicMock

import pytest

from services.answer_policy import information_fallback, non_trip_answer
from services.budget import attach_budget_details, resolve_budget_cap
from services.context_router import decide_context_strategy, is_information_only_request
from services.groq_service import _attach_retrieval_details, _prepare_reply
from services.planning_context import planning_context, validate_planning_context
from services.retrieval_policy import relevant_hits
from services.response_validator import validate_ai_response
from services.transport_policy import validate_transport_claims


INITIAL = "Plan a 2-day trip to Kandy with vegetarian meals and train travel. Include a suggested total budget estimate. Clearly disclose any assumptions about my starting point, number of travellers, nights and travel dates."
STATED = "Plan a 2-day trip to Kandy starting from Galle for 2 adults and 1 child. We will stay 1 night and arrive on 2026-12-10. My total budget is LKR 45000. Keep vegetarian meals and train travel."
EDIT = "Make it 3 days instead and change the overnight stay to 2 nights. Keep the same starting point, travellers, arrival date, vegetarian meals, train preference and total budget."
SAVED_REQUEST = "Plan a 2-day trip to Kandy using my saved budget preference as a guide. Include an estimated total within that range, vegetarian meals and train travel."
SAVED = {"budget_lkr_min": 45000, "budget_lkr_max": 45000, "budget_preference_source": "user_provided"}


def plan(days, origin, total=45000):
    return {"destination": "Kandy", "days": days, "budget_lkr": total,
        "summary": f"A {days}-day Kandy trip with vegetarian meals and train travel.",
        "itinerary": [{"day": n, "title": "Kandy", "items": (
            [f"Plan train travel from {origin} to Kandy; confirm route and connections with the operator", "Vegetarian dinner"]
            if n == 1 else ["Vegetarian breakfast", "Explore Kandy"])} for n in range(1, days + 1)],
        "follow_up_question": None}


def history():
    first = _prepare_reply(INITIAL, plan(2, "Colombo", 36000), MagicMock())
    turns = [{"role": "user", "content": INITIAL}, {"role": "assistant", "content": json.dumps(first)}]
    second = _prepare_reply(STATED, plan(2, "Galle"), MagicMock(), history=turns)
    return turns + [{"role": "user", "content": STATED}, {"role": "assistant", "content": json.dumps(second)}]


@pytest.mark.parametrize("query", [
    EDIT, "Make it 3 days instead with vegetarian meals and train travel.",
    "Change the overnight stay to 2 nights with vegetarian food.",
    "Revise this trip to Kandy to 3 days.",
    "Extend it to 3 days and keep train travel.",
    "I prefer train travel; make it 3 days instead.",
])
def test_plural_days_and_nights_are_itinerary_edits(query):
    assert not is_information_only_request(query)
    assert not non_trip_answer(query)
    assert decide_context_strategy(query, 4).use_conversation_history


def test_reported_third_turn_preserves_days_budget_table_and_trip_details():
    turns = history()
    retry = MagicMock(side_effect=AssertionError("The supplied three-day plan is valid"))
    result = _prepare_reply(EDIT, plan(3, "Galle"), retry, history=turns, user_preferences=SAVED)
    retry.assert_not_called()
    assert result["days"] == 3 and [day["day"] for day in result["itinerary"]] == [1, 2, 3]
    assert result["budget_lkr"] == 45000 and result["budget_cap_lkr"] == 45000
    assert result["budget_breakdown"]["total_lkr"] == 45000
    assert result["budget_source"] == "user_provided"
    context = result["planning_context"]
    assert context["starting_point"] == "Galle" and context["travellers"] == 3
    assert context["nights"] == 2 and context["dates"] == "2026-12-10"


def test_same_chat_keeps_current_cap_while_new_chat_uses_saved_preference():
    turns = history()
    assert resolve_budget_cap(SAVED_REQUEST, turns) == 45000
    assert resolve_budget_cap(SAVED_REQUEST, []) is None
    current = attach_budget_details(plan(2, "Galle"), resolve_budget_cap(SAVED_REQUEST, turns), preferences=SAVED)
    fresh = attach_budget_details(plan(2, "Colombo"), None, preferences=SAVED)
    assert current["budget_source"] == "user_provided"
    assert fresh["budget_source"] == "saved_preference"
    assert planning_context(SAVED_REQUEST, turns)["starting_point"] == "Galle"
    assert planning_context(SAVED_REQUEST, [])["starting_point_source"] == "assumed"


def timetable(origin="Maradana", destination="Galle"):
    return {"score": 0.85, "fields": {"text": f"Train timetable {origin} to {destination} departure 17:25.",
        "route_origin": origin, "route_destination": destination, "source_date": "2025-12-02",
        "extraction_method": "reviewed_transcription", "document_name": "Train-Timetable.pdf"}}


def test_train_preference_does_not_cite_coastal_timetable_for_kandy():
    guide = {"score": 0.7, "fields": {"text": "Kandy offers cultural sights and vegetarian meals.", "document_name": "guide.pdf"}}
    assert relevant_hits(INITIAL, [timetable(), guide], 4) == [guide]
    assert relevant_hits(EDIT + " Destination: Kandy", [timetable(), guide], 4) == [guide]
    assert relevant_hits("Show the train departures in Kandy", [timetable()], 4) == []


def test_explicit_timetable_requests_keep_correct_direction_and_station_names():
    fort = timetable("Colombo Fort", "Matara")
    assert relevant_hits("Show train departures from Colombo Fort", [fort], 4) == [fort]
    assert relevant_hits("Show train departures from Colombo to Matara", [fort], 4) == [fort]
    assert relevant_hits("Show train departures from Galle", [timetable()], 4) == []
    assert relevant_hits("Show Maradana to Galle departures", [timetable()], 4)


def test_stale_timetable_notice_copied_from_history_is_not_displayed():
    data = {"summary": "A Kandy itinerary.\n\nTrain reference: notice dated 2025-12-02 (partial reviewed transcription). Services may have changed."}
    _attach_retrieval_details(data, [{"document_name": "guide.pdf"}])
    assert data["summary"] == "A Kandy itinerary."
    assert data["kb_sources"] == [{"document_name": "guide.pdf"}]


@pytest.mark.parametrize("activity", [
    "Depart Galle railway station by train (approx. 3 h) to Kandy",
    "Afternoon train back to Galle (≈3 h)", "Allow 1.5 hours for the transfer",
    "The bus journey takes three hours", "A direct train from Galle to Kandy",
    "Spend 3 hours on the train to Kandy",
])
def test_unsupported_transport_duration_and_direct_service_is_retried(activity):
    invalid = plan(2, "Galle")
    invalid["itinerary"][0]["items"][0] = activity
    retry = MagicMock(return_value=plan(2, "Galle"))
    result = validate_ai_response(STATED, invalid, retry)
    retry.assert_called_once()
    assert result["itinerary"][0]["items"][0] != activity


def test_activity_time_and_itinerary_days_are_not_transport_duration_claims():
    data = plan(3, "Galle")
    data["itinerary"][1]["items"].append("Spend 2 hours at the gardens before returning by train")
    assert validate_transport_claims(data) == []
    assert validate_transport_claims({"summary": "Maradana to Galle departure 17:25", "itinerary": []}) == []


def test_route_duration_question_does_not_guess_from_generic_rail_guide():
    result = information_fallback("How long is the train journey from Galle to Kandy?",
                                  [{"document_name": "guide.pdf"}], "evidence_found")
    assert "cannot verify" in result["summary"] and "3 hours" not in result["summary"]


def test_summary_nights_and_party_cannot_contradict_preserved_context():
    data = plan(3, "Galle")
    data["summary"] = "A Kandy trip for 2 travellers with a 1-night stay."
    issues = validate_planning_context(EDIT, data, history())
    assert any("nights" in issue for issue in issues)
    assert any("travellers" in issue for issue in issues)
