"""Place spelling, budget provenance, and transparent trip assumptions."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import httpx
from groq import BadRequestError

import pytest

from memory_utils import build_memory_text
from schemas.chat import ChatResponse
from schemas.preferences import UserPreferencesUpdate
from services.budget import attach_budget_details, saved_budget_range, validate_budget_origin
from services.destination_spelling import validate_destination_spelling
from services.groq_service import _build_prompt_messages, _prepare_reply
from services.history_manager import _summarize_messages
from services.planning_context import attach_planning_context, planning_context, validate_planning_context
from services.response_validator import KNOWN_DESTINATIONS, validate_ai_response
from services.user_preferences import extract_and_merge_preferences, format_preferences_context
from services import groq_service

REQUEST = "Plan a 2-day trip to Kandy using my saved food and transport preferences."
PLAN = {
    "destination": "Kandy", "days": 2, "budget_lkr": 40000,
    "summary": "A suggested 2-day Kandy itinerary with vegetarian meals and train travel.",
    "itinerary": [
        {"day": 1, "title": "Kandy", "items": ["Take a train from Colombo to Kandy", "Vegetarian dinner"]},
        {"day": 2, "title": "Kandy sights", "items": ["Visit the gardens", "Return by train"]},
    ], "follow_up_question": None,
}
SAVED = {"budget_lkr_min": 30000, "budget_lkr_max": 45000, "budget_preference_source": "user_provided"}


@pytest.mark.parametrize("typo", ["Kanda", "Kandi", "Kady", "Kadny"])
def test_destination_typo_inside_activity_is_retried(typo):
    bad = copy.deepcopy(PLAN)
    bad["itinerary"][0]["items"][0] = f"Take a train from Colombo to {typo}"
    retry = MagicMock(return_value=copy.deepcopy(PLAN))
    result = validate_ai_response(REQUEST, bad, retry)
    retry.assert_called_once()
    assert any("spelling error" in issue for issue in retry.call_args.args[0])
    assert "to Kandy" in result["itinerary"][0]["items"][0]


def test_unrepaired_destination_typo_is_not_shown():
    bad = copy.deepcopy(PLAN)
    bad["itinerary"][1]["title"] = "Kanda sightseeing"
    result = validate_ai_response(REQUEST, bad, lambda _: bad)
    assert not result["itinerary"]
    assert "Kanda" not in result["summary"]


def test_legitimate_other_cities_and_explicit_user_place_names_are_not_autocorrected():
    data = copy.deepcopy(PLAN)
    data["itinerary"][0]["items"] = ["Travel from Galle to Kandy", "Sample local candy"]
    assert validate_destination_spelling(REQUEST, data, KNOWN_DESTINATIONS) == []
    data["itinerary"][0]["items"].append("Visit Kanda")
    assert validate_destination_spelling("Visit Kanda and Kandy", data, KNOWN_DESTINATIONS) == []


@pytest.mark.parametrize("cap,prefs,source", [
    (45000, None, "user_provided"), (None, SAVED, "saved_preference"),
    (None, None, "estimate"),
    (None, {"budget_lkr_min": 30000, "budget_lkr_max": 45000}, "estimate"),
    (None, {**SAVED, "budget_lkr_max": 35000}, "estimate"),
])
def test_budget_origin_is_server_owned(cap, prefs, source):
    forged = {**PLAN, "budget_source": "user_provided", "budget_note": "Fake provenance", "budget_cap_lkr": 99999}
    result = attach_budget_details(forged, cap, preferences=prefs)
    assert result["budget_source"] == source
    assert result["budget_note"] != "Fake provenance"
    assert result.get("budget_cap_lkr") == cap
    assert sum(result["budget_breakdown"][key] for key in ["accommodation_lkr", "food_lkr", "transport_lkr", "activities_lkr", "misc_lkr"]) == 40000
    assert ChatResponse.model_validate(result).budget_source == source


@pytest.mark.parametrize("summary", [
    "Fits your usual budget range.", "Fits your saved budget preference.",
    "Within your budget.", "Within your LKR 40,000 budget.",
])
def test_invented_user_budget_claim_is_rejected(summary):
    assert validate_budget_origin({**PLAN, "summary": summary})


@pytest.mark.parametrize("summary", [
    "No saved budget was provided; this is a suggested estimate.",
    "Your budget is not specified. This is an estimate.",
    "A suggested planning estimate for this trip.",
])
def test_explicit_budget_uncertainty_is_not_rejected(summary):
    assert validate_budget_origin({**PLAN, "summary": summary}) == []


def test_saved_budget_range_is_advisory_not_a_current_cap():
    assert validate_budget_origin({**PLAN, "summary": "Fits your saved budget preference."}, preferences=SAVED) == []
    assert validate_budget_origin({**PLAN, "budget_lkr": 50000, "summary": "Fits your saved budget preference."}, preferences=SAVED)
    assert validate_budget_origin({**PLAN, "summary": "Within your budget."}, preferences=SAVED)


def test_estimates_are_never_saved_as_personal_budget_preferences():
    prefs = extract_and_merge_preferences({}, REQUEST, PLAN)
    assert prefs.get("budget_lkr_min") is None and prefs.get("budget_lkr_max") is None
    assert "budget" not in build_memory_text(REQUEST, PLAN).lower()
    prefs = extract_and_merge_preferences(prefs, REQUEST + " My total budget is LKR 45000.", PLAN)
    assert saved_budget_range(prefs) == (45000, 45000)
    # A cheaper generated allocation is not a new user preference.
    prefs = extract_and_merge_preferences(prefs, "Make day 2 more relaxed", {**PLAN, "budget_lkr": 20000})
    assert saved_budget_range(prefs) == (45000, 45000)


def test_legacy_estimated_range_is_not_merged_into_a_confirmed_preference():
    old = {"budget_lkr_min": 1000, "budget_lkr_max": 99999}
    assert saved_budget_range(old) is None
    assert "budget" not in format_preferences_context(old).lower()
    updated = extract_and_merge_preferences(old, "My budget is LKR 45000", PLAN)
    assert saved_budget_range(updated) == (45000, 45000)


def test_manual_settings_budget_has_confirmed_provenance():
    stored = UserPreferencesUpdate(budget_lkr_min=45000, budget_lkr_max=30000).to_stored_dict()
    assert saved_budget_range(stored) == (30000, 45000)
    assert saved_budget_range(UserPreferencesUpdate().to_stored_dict()) is None


def test_history_summary_does_not_turn_model_estimates_into_user_budget():
    record = SimpleNamespace(destination="Kandy", days=2, budget_lkr=40000,
                             user_message=REQUEST, assistant_reply=json.dumps(PLAN))
    summary = _summarize_messages([record])
    assert "User-stated total budget cap: Not specified" in summary
    assert "estimate (not proof of a user preference): 40000 LKR" in summary


def test_missing_trip_details_are_explicit_assumptions():
    result = attach_planning_context(PLAN, REQUEST)
    context = result["planning_context"]
    assert context["starting_point"] == "Colombo" and context["starting_point_source"] == "assumed"
    assert context["travellers"] == 1 and context["travellers_source"] == "assumed"
    assert context["nights"] == 1 and context["nights_source"] == "assumed"
    assert context["dates"] is None
    assert len(result["assumptions"]) == 4
    assert ChatResponse.model_validate(result).planning_context.nights == 1


def test_user_details_replace_assumptions():
    message = "Plan a 2-day trip to Kandy starting from Galle for 2 adults and 1 child, 1 night, on 2026-12-10."
    context = planning_context(message, days=2)
    assert context["starting_point"] == "Galle" and context["starting_point_source"] == "user"
    assert context["travellers"] == 3 and context["nights"] == 1
    assert context["dates"] == "2026-12-10"
    assert attach_planning_context(PLAN, message)["assumptions"] == []


def test_party_correction_is_not_added_to_the_old_count():
    assert planning_context("Plan a trip for 2 people. Change it to 4 people.")["travellers"] == 4


def test_current_origin_overrides_history_and_supports_non_catalogue_origins():
    history = [{"role": "user", "content": "Start from Galle"}]
    context = planning_context("My starting point is Colombo. Actually, start from New Delhi.", history)
    assert context["starting_point"] == "New Delhi"


def test_assumed_origin_never_becomes_user_stated_after_history_trimming():
    previous = attach_planning_context(PLAN, REQUEST)
    history = [{"role": "assistant", "content": json.dumps(previous)}]
    context = planning_context("Make it 3 days instead", history)
    assert context["starting_point_source"] == "assumed"
    assert context["nights"] == 2
    previous["planning_context"].update(starting_point="Galle", starting_point_source="user")
    context = planning_context("Make it 3 days instead", [{"role": "assistant", "content": json.dumps(previous)}])
    assert context["starting_point"] == "Galle" and context["starting_point_source"] == "user"


def test_do_not_assume_instruction_keeps_missing_details_unknown():
    context = planning_context(REQUEST + " Do not assume my starting point or traveller count.", days=2)
    assert context["starting_point"] is None and context["travellers"] is None
    assert planning_context("My starting point is unknown.")["starting_point"] is None
    history = [{"role": "user", "content": "Start from Galle"},
               {"role": "user", "content": "My starting point is unknown."}]
    assert planning_context("Make it 3 days", history)["starting_point"] is None
    previous = attach_planning_context(PLAN, REQUEST + " Do not assume anything.")
    trimmed = [{"role": "assistant", "content": json.dumps(previous)}]
    assert planning_context("Make it 3 days", trimmed)["starting_point"] is None


def test_wrong_first_departure_is_rejected_and_zero_nights_has_no_checkin():
    assert validate_planning_context(REQUEST + " Start from Galle.", PLAN)
    no_nights = copy.deepcopy(PLAN)
    no_nights["itinerary"][0]["items"].append("Check-in at a guesthouse")
    assert validate_planning_context(REQUEST + " We need 0 nights.", no_nights)


def test_colombo_fort_station_is_allowed_for_a_colombo_start():
    data = copy.deepcopy(PLAN)
    data["itinerary"][0]["items"][0] = "Take a train from Colombo Fort station to Kandy"
    assert validate_planning_context(REQUEST, data) == []


def test_complete_reply_uses_all_three_safeguards():
    forged = {**copy.deepcopy(PLAN), "assumptions": ["Fake assumption"],
              "planning_context": {"starting_point": "Galle", "starting_point_source": "user"}}
    result = _prepare_reply(REQUEST, forged, MagicMock(side_effect=AssertionError("No retry")))
    assert result["budget_source"] == "estimate"
    assert result["planning_context"]["starting_point_source"] == "assumed"
    assert result["assumptions"]
    assert "Fake assumption" not in result["assumptions"]
    assert ChatResponse.model_validate(result).days == 2


def test_day_trip_has_no_accommodation_allocation_and_keeps_exact_total():
    plan = {**PLAN, "days": 1, "itinerary": [PLAN["itinerary"][0]]}
    result = _prepare_reply("Plan a 1-day trip to Kandy", plan, MagicMock(side_effect=AssertionError("No retry")))
    assert result["planning_context"]["nights"] == 0
    assert result["budget_breakdown"]["accommodation_lkr"] == 0
    assert sum(result["budget_breakdown"][key] for key in ["accommodation_lkr", "food_lkr", "transport_lkr", "activities_lkr", "misc_lkr"]) == 40000


def test_prompt_distinguishes_user_details_and_assumptions_without_external_calls():
    messages, _, _ = _build_prompt_messages(REQUEST, None, [], False, False, "test", candidate_tools=[], user_preferences=SAVED)
    assert "Saved user-provided budget preference" in messages[0]["content"]
    assert '"starting_point_source": "assumed"' in messages[0]["content"]


@pytest.mark.parametrize("mode", ["nonstream", "stream", "stream_recovery"])
def test_generation_and_stream_recovery_keep_server_provenance(monkeypatch, mode):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setattr(groq_service, "_build_prompt_messages", lambda *_a, **_k: ([{"role": "user", "content": REQUEST}], [], []))
    data = {**copy.deepcopy(PLAN), "summary": "A Kandy itinerary fitting your saved budget preference."}
    def create(**kwargs):
        if kwargs.get("stream"):
            if mode == "stream_recovery":
                raise BadRequestError("Invalid tool output", response=httpx.Response(400,
                    request=httpx.Request("POST", "https://api.groq.com/test")), body={"error": {"code": "tool_use_failed"}})
            return (chunk for chunk in [SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=json.dumps(data)), finish_reason="stop")])])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)), finish_reason="stop")])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(groq_service, "Groq", lambda **_: client)
    if mode == "nonstream":
        result = groq_service.get_groq_reply(REQUEST, user_preferences=SAVED)
    else:
        result = next(value for event, value in groq_service.stream_groq_reply(REQUEST, user_preferences=SAVED) if event == "done")
    assert result["budget_source"] == "saved_preference"
    assert result["planning_context"]["starting_point_source"] == "assumed"
    assert result["assumptions"]
