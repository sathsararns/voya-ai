"""A follow-up needs a real current plan; saved budgets need confirmed data."""

import copy
import json
from unittest.mock import MagicMock

import pytest

from services import groq_service
from services.answer_policy import planning_preflight_reply
from services.budget import resolve_budget_cap, validate_saved_budget_request
from services.planning_context import planning_context
from services.response_validator import validate_ai_response
from services.trip_state import scoped_trip_history
from services.venue_facts import validate_required_venues


EDIT = "Make it 3 days instead and change the overnight stay to 2 nights. Keep the same starting point, travellers, arrival date, vegetarian meals, train preference and total budget."
REQUEST = "Plan a 2-day trip to Kandy using my saved budget preference as a guide. Include an estimated total within that range, vegetarian meals and train travel."
SAVED = {"budget_lkr_min": 45000, "budget_lkr_max": 45000, "budget_preference_source": "user_provided"}
PLAN = {"destination": "Kandy", "days": 2, "budget_lkr": 45000,
        "summary": "A 2-day Kandy trip with vegetarian meals and train travel.",
        "itinerary": [{"day": 1, "title": "Kandy arrival", "items": ["Train travel from Colombo to Kandy", "Vegetarian dinner"]},
                      {"day": 2, "title": "Kandy sights", "items": ["Visit the gardens"]}],
        "follow_up_question": None}


def turn(data):
    return {"role": "assistant", "content": json.dumps(data)}


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("history", [[], [turn({"summary": "An earlier reply failed.", "itinerary": [], "error_code": "validation_failed"})]])
def test_orphan_same_trip_edit_asks_for_previous_plan_without_api_calls(monkeypatch, streaming, history):
    model = MagicMock(side_effect=AssertionError("Must not generate an unrelated trip"))
    search = MagicMock(side_effect=AssertionError("No KB lookup needed for missing chat context"))
    monkeypatch.setattr(groq_service, "Groq", model)
    monkeypatch.setattr(groq_service, "search_knowledge_base", search)
    if streaming:
        reply = next(data for event, data in groq_service.stream_groq_reply(EDIT, history=history, user_preferences=SAVED) if event == "done")
    else:
        reply = groq_service.get_groq_reply(EDIT, history=history, user_preferences=SAVED)
    assert reply["itinerary"] == [] and reply["kb_sources"] == []
    assert "previous itinerary" in reply["summary"]
    assert "Which trip" in reply["follow_up_question"]
    model.assert_not_called()
    search.assert_not_called()


def test_followup_cannot_silently_switch_from_kandy_to_coastal_trip():
    original = {**copy.deepcopy(PLAN), "budget_cap_lkr": 45000}
    invalid = {**copy.deepcopy(PLAN), "destination": "Galle and Bentota", "days": 3,
               "summary": "A 3-day coastal trip.", "itinerary": [{"day": 1, "title": "Coast", "items": ["Visit Galle"]}]}
    corrected = {**copy.deepcopy(PLAN), "days": 3}
    corrected["itinerary"].append({"day": 3, "title": "Kandy", "items": ["Vegetarian lunch"]})
    retry = MagicMock(return_value=corrected)
    result = validate_ai_response(EDIT, invalid, retry, history=[turn(original)], user_preferences=SAVED)
    assert retry.called and any("Preserve its destination" in issue for issue in retry.call_args.args[0])
    assert result["destination"] == "Kandy" and result["days"] == 3


def test_keep_total_budget_adopts_a_previous_estimate_only_when_explicitly_requested():
    estimate = {**PLAN, "budget_lkr": 36000}
    assert resolve_budget_cap("Make it 3 days and keep the same total budget", [turn(estimate)]) == 36000
    assert resolve_budget_cap("Make it 3 days instead", [turn(estimate)]) is None
    assert planning_preflight_reply(EDIT, [turn({**PLAN, "budget_lkr": None})], SAVED)["itinerary"] == []


def test_new_destination_does_not_inherit_old_required_coastal_venues_or_cap():
    old = {**copy.deepcopy(PLAN), "destination": "Galle, Mirissa, Bentota", "budget_cap_lkr": 30000}
    history = [{"role": "user", "content": "Plan a trip to Galle, Mirissa and Bentota. Include Brief Garden and Galle National Museum. My budget is LKR 30000."}, turn(old)]
    assert scoped_trip_history(REQUEST, history) == []
    assert resolve_budget_cap(REQUEST, history) is None
    assert validate_required_venues(REQUEST, PLAN, history) == []
    context = planning_context(REQUEST, history, 2)
    assert context["starting_point_source"] == "assumed"
    extended = history + [{"role": "user", "content": REQUEST}, turn(PLAN)]
    assert validate_required_venues("Make it 3 days", PLAN, extended) == []


def test_changing_duration_recalculates_old_nights_with_disclosure():
    old = {**copy.deepcopy(PLAN), "days": 3, "planning_context": {
        "starting_point": "Galle", "starting_point_source": "user", "travellers": 3, "travellers_source": "user",
        "nights": 2, "nights_source": "user", "dates": "2026-12-10", "dates_source": "user"}}
    context = planning_context(REQUEST, [turn(old)], 2)
    assert context["nights"] == 1 and context["nights_source"] == "assumed"
    assert context["starting_point"] == "Galle" and context["travellers"] == 3
    keep = planning_context(REQUEST + " Keep the same overnight stays.", [turn(old)], 2)
    assert keep["nights"] == 2 and keep["nights_source"] == "user"
    stated = planning_context(REQUEST + " We need a 1-night stay.", [turn(old)], 2)
    assert stated["nights"] == 1 and stated["nights_source"] == "user"


def test_unapplied_orphan_edit_does_not_pollute_a_subsequent_kandy_plan():
    clarification = planning_preflight_reply(EDIT, [], SAVED)
    history = [{"role": "user", "content": EDIT}, turn(clarification)]
    context = planning_context(REQUEST, history, 2)
    assert context["nights"] == 1 and context["nights_source"] == "assumed"
    assert context["starting_point_source"] == "assumed"
    assert resolve_budget_cap(REQUEST, history) is None


@pytest.mark.parametrize("preferences", [None, {}, {"budget_lkr_min": 45000, "budget_lkr_max": 45000}])
def test_missing_or_unconfirmed_saved_budget_asks_for_the_amount(preferences):
    result = planning_preflight_reply(REQUEST, [], preferences)
    assert result["itinerary"] == []
    assert "confirmed saved budget" in result["summary"]
    assert result["follow_up_question"] and result["kb_sources"] == []


def test_current_cap_and_saved_range_conflict_is_clarified_without_overriding_either():
    result = planning_preflight_reply(REQUEST, [{"role": "user", "content": "My total budget is LKR 30000"}], SAVED)
    assert result and result["itinerary"] == []
    assert "30,000" in result["summary"] and "45,000" in result["summary"]


def test_current_explicit_budget_overrides_saved_range_without_impossible_bounds():
    message = REQUEST + " My total budget for this trip is LKR 30000 instead."
    assert planning_preflight_reply(message, [], SAVED) is None
    data = {**copy.deepcopy(PLAN), "budget_lkr": 30000}
    assert validate_saved_budget_request(message, data, SAVED) == []
    result = groq_service._prepare_reply(message, data, MagicMock(side_effect=AssertionError("No retry")), user_preferences=SAVED)
    assert result["budget_source"] == "user_provided" and result["budget_cap_lkr"] == 30000


def test_saved_budget_request_retries_a_missing_total_instead_of_claiming_an_allowance():
    invalid = {**copy.deepcopy(PLAN), "budget_lkr": None,
               "summary": "This Kandy trip fits your usual planning allowance."}
    retry = MagicMock(return_value=copy.deepcopy(PLAN))
    result = groq_service._prepare_reply(REQUEST, invalid, retry, user_preferences=SAVED)
    retry.assert_called_once()
    assert result["budget_lkr"] == 45000 and result["budget_source"] == "saved_preference"
    assert result["budget_breakdown"]["total_lkr"] == 45000


def test_invalid_saved_budget_plan_is_a_logged_error_without_misleading_sources(caplog):
    invalid = {**copy.deepcopy(PLAN), "budget_lkr": None,
               "summary": "This trip fits your usual planning allowance. PRIVATE_DIAGNOSTIC_TEST"}
    result = groq_service._prepare_reply(REQUEST, invalid, lambda _: invalid, user_preferences=SAVED)
    groq_service._attach_retrieval_details(result, [{"document_name": "Guide-Book-Sri-Lanka.pdf"}])
    assert result["error_code"] == "validation_failed"
    assert result["itinerary"] == [] and result["kb_sources"] == []
    assert result["follow_up_question"] is None
    assert "saved_budget_request" in caplog.text
    assert "PRIVATE_DIAGNOSTIC_TEST" not in caplog.text


@pytest.mark.parametrize("amount", [None, 44000, 46000])
def test_saved_range_request_requires_numeric_total_inside_the_confirmed_range(amount):
    assert validate_saved_budget_request(REQUEST, {**PLAN, "budget_lkr": amount}, SAVED)
    assert validate_saved_budget_request(REQUEST, PLAN, SAVED) == []
