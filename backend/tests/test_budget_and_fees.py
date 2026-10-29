"""Hard budget caps, exact planning allocations, and unverified fee claims."""

import copy
import json
from unittest.mock import MagicMock

import pytest

from schemas.chat import ChatResponse
from services.budget import attach_budget_details, calculate_budget, extract_budget_cap, resolve_budget_cap
from services.fee_policy import FEE_NOTICE, validate_fee_claims
from services.groq_service import _prepare_reply
from services.response_validator import matches_request, validate_ai_response


PLAN = {
    "destination": "Galle, Mirissa and Bentota", "days": 3, "budget_lkr": 45000,
    "summary": "A coastal itinerary with estimated costs.",
    "itinerary": [{"day": 1, "title": "Galle", "items": ["Explore the fort"]}],
    "follow_up_question": None,
}


@pytest.mark.parametrize("message,expected", [
    ("My total budget is LKR 45000", 45000),
    ("Keep my budget under Rs. 45,000", 45000),
    ("My budget is 45000 rupees", 45000),
    ("Budget 45k", 45000),
    ("I can spend LKR 30,000", 30000),
    ("LKR 500", 500),
    ("My budget is LKR 0", 0),
    ("Make it 3 days instead", None),
    ("I travel in 2026", None),
    ("Is admission LKR 3000?", None),
    ("Total entry fee LKR 3000?", None),
    ("Accommodation is LKR 5000 per night", None),
    ("My budget is USD 200", None),
])
def test_budget_cap_extraction(message, expected):
    assert extract_budget_cap(message) == expected


@pytest.mark.parametrize("total", [45000, 45001, 999, 1])
@pytest.mark.parametrize("style", ["budget", "mid-range", "luxury"])
def test_allocation_sum_is_exact_without_rounding_overspend(total, style):
    result = calculate_budget(3, total, style)
    assert sum(result["breakdown_lkr"].values()) == total
    assert all(value >= 0 for value in result["breakdown_lkr"].values())
    assert result["is_estimate"] is True


def test_45000_budget_allocation():
    result = attach_budget_details(PLAN, 45000)
    breakdown = result["budget_breakdown"]
    assert breakdown == {
        "accommodation_lkr": 13500, "food_lkr": 13500,
        "transport_lkr": 11250, "activities_lkr": 4500, "misc_lkr": 2250,
        "total_lkr": 45000, "is_estimate": True,
    }
    assert ChatResponse.model_validate(result).budget_breakdown.total_lkr == 45000


@pytest.mark.parametrize("amount", [45001, 50000, 51750])
def test_any_amount_above_cap_is_rejected(amount):
    data = {**PLAN, "budget_lkr": amount}
    assert matches_request("My total budget is LKR 45000", data)


def test_lower_estimate_is_allowed_but_missing_total_is_not():
    assert matches_request("My budget is LKR 45000", {**PLAN, "budget_lkr": 40000}) == []
    assert matches_request("My budget is LKR 45000", {**PLAN, "budget_lkr": None})


def test_budget_limit_survives_followups_and_recent_history_trimming():
    history = [
        {"role": "user", "content": "My total budget is LKR 45000"},
        {"role": "assistant", "content": json.dumps({**PLAN, "budget_cap_lkr": 45000})},
        {"role": "user", "content": "Make day 2 more relaxed"},
    ]
    assert resolve_budget_cap("Add vegetarian meals", history) == 45000
    assert resolve_budget_cap("Add vegetarian meals", history[1:]) == 45000
    assert resolve_budget_cap("Increase my budget to LKR 60000", history) == 60000
    assert resolve_budget_cap("Show me travel options", history) is None
    assert resolve_budget_cap("Plan the trip", [
        {"role": "assistant", "content": json.dumps(PLAN)},
    ]) is None


def test_failed_retry_cannot_salvage_an_overbudget_plan():
    bad = {**PLAN, "budget_lkr": 50000, "summary": "Spend LKR 50,000 on this plan."}
    retry = MagicMock(return_value=bad)
    result = validate_ai_response("My budget is LKR 45000", bad, retry)

    retry.assert_called_once()
    assert result["budget_lkr"] is None
    assert result["itinerary"] == []
    assert result["budget_cap_lkr"] == 45000
    assert "50,000" not in result["summary"]


def test_followup_cannot_raise_a_previously_set_budget():
    history = [{"role": "user", "content": "My budget is LKR 45000"}]
    bad = {**PLAN, "budget_lkr": 50000}
    retry = MagicMock(return_value=copy.deepcopy(PLAN))
    result = validate_ai_response("Make day 2 relaxed", bad, retry, history=history)

    retry.assert_called_once()
    assert result["budget_lkr"] == 45000


@pytest.mark.parametrize("text", [
    "Visit Brief Garden (free entry)",
    "Visit the museum (free on certain days)",
    "Free attractions include Brief Garden",
    "Gentle snorkeling (no-cost activity)",
    "Visit Brief Garden (low entrance fee)",
    "Museum entry costs LKR 100",
    "Pay Rs 100 admission",
])
def test_unsupported_cost_claims_are_rejected(text):
    data = copy.deepcopy(PLAN)
    data["itinerary"][0]["items"] = [text]
    assert validate_fee_claims(data)


@pytest.mark.parametrize("text", [
    "Free time at Mirissa Beach",
    "Enjoy a gluten-free vegetarian lunch",
    "Visit Brief Garden (entry fee unverified; confirm with the venue)",
    "Walk along the fort ramparts",
])
def test_unverified_fee_notice_and_nonprice_uses_of_free_are_allowed(text):
    data = copy.deepcopy(PLAN)
    data["itinerary"][0]["items"] = [text]
    assert validate_fee_claims(data) == []


def test_invalid_fee_claims_are_not_salvaged_after_failed_retry():
    data = copy.deepcopy(PLAN)
    data["summary"] = "Brief Garden is free."
    result = validate_ai_response("Plan the trip", data, lambda _: data)

    assert "Brief Garden is free" not in result["summary"]
    assert result["itinerary"] == []


def test_server_adds_breakdown_and_fee_notice_to_a_valid_reply():
    retry = MagicMock(side_effect=AssertionError("No correction needed"))
    result = _prepare_reply("My budget is LKR 45000", copy.deepcopy(PLAN), retry)

    assert result["budget_breakdown"]["total_lkr"] == 45000
    assert result["budget_cap_lkr"] == 45000
    assert result["fee_notice"] == FEE_NOTICE
    assert ChatResponse.model_validate(result).budget_breakdown.is_estimate is True


def test_legacy_saved_replies_need_no_new_fields():
    parsed = ChatResponse.model_validate(PLAN)
    assert parsed.budget_breakdown is None
    assert parsed.fee_notice is None
