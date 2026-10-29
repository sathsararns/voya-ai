"""Regressions for conflicting item prices and incorrect venue placement."""

import copy
from unittest.mock import MagicMock

import pytest

from services.budget import attach_budget_details, validate_budget_presentation
from services.groq_service import _build_prompt_messages, _prepare_reply
from services.response_validator import KNOWN_DESTINATIONS, validate_ai_response
from services.venue_facts import validate_required_venues, validate_venue_placement


REQUEST = (
    "Plan a 3-day solo trip to Galle, Mirissa and Bentota. My total budget is LKR 45000, "
    "including accommodation, food, transport and activities. Include Brief Garden and "
    "Galle National Museum. Do not guess entrance fees."
)
PLAN = {
    "destination": "Galle, Mirissa and Bentota", "days": 3, "budget_lkr": 45000,
    "summary": "A 3-day coastal itinerary with a total estimated budget of LKR 45,000.",
    "itinerary": [
        {"day": 1, "title": "Galle exploration", "items": [
            "Visit Galle National Museum (entry fee unverified; confirm with the venue)",
            "Enjoy a vegetarian lunch at a local cafe",
        ]},
        {"day": 2, "title": "Relaxed Mirissa day", "items": ["Walk along Mirissa Beach"]},
        {"day": 3, "title": "Bentota and Beruwala", "items": [
            "Visit Brief Garden (entry fee unverified; confirm with the venue)",
        ]},
    ],
    "follow_up_question": None,
}


@pytest.mark.parametrize("item", [
    "Vegetarian lunch (LKR 2,000)",
    "Vegetarian lunch (2,000 LKR)",
    "Breakfast estimated Rs. 1500",
    "Check in at a guesthouse (estimated LKR\u202f13,500)",
    "Dinner estimated 2k rupees",
])
def test_inline_prices_cannot_form_a_second_inconsistent_ledger(item):
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"].append(item)
    assert validate_budget_presentation(plan)


def test_reported_food_14500_vs_allocation_13500_is_rejected():
    plan = copy.deepcopy(PLAN)
    prices = [[1500, 2000, 500, 2000], [2000, 2500], [2000, 2000]]
    for day, amounts in zip(plan["itinerary"], prices):
        day["items"].extend(f"Vegetarian meal (LKR {amount:,})" for amount in amounts)
    assert sum(sum(day) for day in prices) == 14500
    assert attach_budget_details(plan, 45000)["budget_breakdown"]["food_lkr"] == 13500

    retry = MagicMock(return_value=copy.deepcopy(PLAN))
    result = _prepare_reply(REQUEST, plan, retry)

    retry.assert_called_once()
    assert validate_budget_presentation(result) == []
    assert result["budget_breakdown"]["food_lkr"] == 13500
    assert result["itinerary"] == PLAN["itinerary"]


@pytest.mark.parametrize("item", [
    "Breakfast at the guesthouse (included)",
    "Included breakfast and hotel transfer",
])
def test_booking_inclusions_are_not_invented(item):
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"].append(item)
    assert validate_budget_presentation(plan)


def test_nonprice_numbers_and_trip_total_remain_allowed():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][1]["items"].append("Allow 1.5 hours for the transfer; arrive at 10:30")
    assert validate_budget_presentation(plan) == []


def test_summary_cannot_repeat_a_conflicting_food_allowance():
    plan = copy.deepcopy(PLAN)
    plan["summary"] += " Food costs LKR 14,500."
    assert validate_budget_presentation(plan)


def test_budget_reduction_can_mention_the_previous_user_total():
    plan = copy.deepcopy(PLAN)
    plan["budget_lkr"] = 30000
    plan["summary"] = "Reduced the estimated total from LKR 45,000 to LKR 30,000."
    history = [{"role": "user", "content": "My total budget is LKR 45000"}]
    assert validate_budget_presentation(plan, history) == []


def test_stale_breakdown_is_replaced_with_the_server_calculation():
    plan = {**copy.deepcopy(PLAN), "budget_breakdown": {"food_lkr": 14500, "total_lkr": 99999}}
    result = attach_budget_details(plan, 45000)
    assert result["budget_breakdown"]["food_lkr"] == 13500
    assert result["budget_breakdown"]["total_lkr"] == 45000


@pytest.mark.parametrize("title", ["Galle exploration", "Relaxed Mirissa day", "Brief Garden in Galle"])
def test_brief_garden_cannot_be_silently_placed_in_the_wrong_area(title):
    plan = copy.deepcopy(PLAN)
    plan["itinerary"] = [{"day": 1, "title": title, "items": ["Visit Brief Garden"]}]
    issues = validate_venue_placement(plan, KNOWN_DESTINATIONS)
    assert issues
    assert any("Beruwala" in issue for issue in issues)


@pytest.mark.parametrize("title", ["Bentota relaxation", "Beruwala gardens", "Kalawila visit"])
def test_brief_garden_on_its_local_day_is_valid(title):
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][2]["title"] = title
    assert validate_venue_placement(plan, KNOWN_DESTINATIONS) == []


def test_explicit_day_trip_transfer_is_allowed():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"].extend([
        "Drive from Galle to Beruwala for a garden excursion",
        "Visit Brief Garden",
    ])
    assert validate_venue_placement(plan, KNOWN_DESTINATIONS) == []


def test_unrelated_transfer_cannot_excuse_wrong_placement():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"].extend([
        "Travel to Mirissa", "Visit Brief Garden near Bentota",
    ])
    assert validate_venue_placement(plan, KNOWN_DESTINATIONS)


def test_invented_brief_garden_extension_is_rejected():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][2]["items"] = ["Visit the Brief Garden extension near Bentota"]
    assert validate_venue_placement(plan, KNOWN_DESTINATIONS)


def test_summary_cannot_locate_brief_garden_in_galle():
    plan = copy.deepcopy(PLAN)
    plan["summary"] = "Cultural sights in Galle (including Brief Garden and Galle National Museum)."
    assert validate_venue_placement(plan, KNOWN_DESTINATIONS)


def test_correction_cannot_silently_drop_a_required_venue():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][2]["items"] = ["Relax on Bentota Beach"]
    assert validate_required_venues(REQUEST, plan)
    assert validate_required_venues("Make day 2 relaxed", plan, [
        {"role": "user", "content": REQUEST},
    ])
    assert validate_required_venues("Skip Brief Garden", plan, [
        {"role": "user", "content": REQUEST},
    ]) == []


def test_all_semantic_issues_reach_one_correction_round():
    bad = copy.deepcopy(PLAN)
    bad["itinerary"][0]["items"].extend([
        "Visit Brief Garden", "Vegetarian lunch (LKR 2,000)",
    ])
    retry = MagicMock(return_value=copy.deepcopy(PLAN))
    result = validate_ai_response(REQUEST, bad, retry)

    issues = retry.call_args.args[0]
    assert any("unaccounted price" in issue for issue in issues)
    assert any("misplaces Brief Garden" in issue for issue in issues)
    retry.assert_called_once()
    assert result == PLAN


def test_persistent_errors_do_not_reach_the_ui_or_claim_budget_infeasibility():
    bad = copy.deepcopy(PLAN)
    bad["itinerary"][0]["items"].append("Visit Brief Garden")
    result = validate_ai_response(REQUEST, bad, lambda _: bad)

    assert result["itinerary"] == []
    assert "consistent itinerary" in result["summary"]
    assert "within your" not in result["summary"]
    assert result["budget_cap_lkr"] == 45000


def test_generation_prompt_has_sourced_locations_and_one_budget_ledger():
    messages, _, _ = _build_prompt_messages(
        REQUEST, "test-session", None, use_memory=False,
        use_knowledge_base=False, api_key="test-key", candidate_tools=[],
    )
    prompt = messages[0]["content"]
    assert "https://www.briefgarden.com/home" in prompt
    assert "Beruwala" in prompt
    assert "single authoritative estimated budget breakdown" in prompt
    assert "Do not invent or repeat separate meal" in prompt
