"""An 'add another attraction' edit must add an actual new named visit."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services import groq_service
from services.itinerary_edits import addition_prompt_context, validate_attraction_addition
from services.response_validator import validate_ai_response


REQUEST = (
    "Add one more cultural attraction without increasing my total budget. "
    "Keep the trip at 3 days and retain Brief Garden and Galle National Museum."
)
PLAN = {
    "destination": "Galle, Mirissa and Bentota", "days": 3, "budget_lkr": 30000,
    "budget_cap_lkr": 30000,
    "summary": "A 3-day coastal itinerary within the LKR 30,000 total budget.",
    "itinerary": [
        {"day": 1, "title": "Galle Cultural Day", "items": [
            "Visit Galle National Museum (entry fee unverified)",
            "Explore Galle Fort walls, lighthouse and historic streets",
            "Enjoy a vegetarian lunch at an affordable local cafe",
        ]},
        {"day": 2, "title": "Mirissa Relaxation Day", "items": ["Relax on Mirissa Beach"]},
        {"day": 3, "title": "Bentota and Brief Garden", "items": [
            "Visit Brief Garden in Kalawila village, Beruwala (entry fee unverified)",
            "Relax on Bentota Beach",
        ]},
    ],
    "follow_up_question": None,
}
HISTORY = [
    {"role": "user", "content": "My total budget is LKR 30000. Include Brief Garden and Galle National Museum."},
    {"role": "assistant", "content": json.dumps(PLAN)},
]


def with_church():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"].append("Visit Dutch Reformed Church in Galle (entry fee unverified)")
    return plan


def test_exact_reported_unchanged_response_is_rejected():
    issues = validate_attraction_addition(REQUEST, PLAN, HISTORY)
    assert any("addition is missing" in issue for issue in issues)


def test_rephrasing_an_existing_visit_is_not_a_new_attraction():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"][0] = "Explore the National Museum of Galle"
    assert validate_attraction_addition(REQUEST, plan, HISTORY)


@pytest.mark.parametrize("summary,title", [
    ("Added Dutch Reformed Church to your itinerary", "Galle Cultural Day"),
    (PLAN["summary"], "Galle and Dutch Reformed Church"),
])
def test_a_summary_or_day_title_cannot_stand_in_for_an_added_visit(summary, title):
    plan = copy.deepcopy(PLAN)
    plan["summary"] = summary
    plan["itinerary"][0]["title"] = title
    assert validate_attraction_addition(REQUEST, plan, HISTORY)


def test_new_named_visit_on_the_same_route_is_valid():
    plan = with_church()
    assert validate_attraction_addition(REQUEST, plan, HISTORY) == []
    assert validate_ai_response(REQUEST, plan, MagicMock(), history=HISTORY) == plan


def test_previous_alias_does_not_count_as_a_new_visit():
    previous = with_church()
    history = [{"role": "assistant", "content": json.dumps(previous)}]
    plan = copy.deepcopy(previous)
    plan["itinerary"][0]["items"][-1] = "Visit Groote Kerk in Galle"
    assert validate_attraction_addition(REQUEST, plan, history)


def test_aliases_of_the_same_new_place_count_only_once():
    plan = with_church()
    plan["itinerary"][0]["items"].append("Visit Groote Kerk in Galle")
    request = "Add two more cultural attractions. Keep the same duration."
    assert validate_attraction_addition(request, plan, HISTORY)
    plan["itinerary"][0]["items"].append("Visit Galle Maritime Museum (entry fee unverified)")
    assert validate_attraction_addition(request, plan, HISTORY) == []


def test_addition_cannot_replace_an_existing_required_visit():
    plan = with_church()
    plan["itinerary"][2]["items"] = ["Relax on Bentota Beach"]
    assert any("silently remove" in issue for issue in validate_attraction_addition(REQUEST, plan, HISTORY))
    assert validate_attraction_addition("Add one more cultural attraction and skip Brief Garden", plan, HISTORY) == []


def test_addition_cannot_extend_the_trip_with_a_fourth_day():
    plan = with_church()
    plan["itinerary"].append({"day": 4, "title": "Extra day", "items": ["Explore Galle"]})
    assert any("exactly 3" in issue for issue in validate_attraction_addition(REQUEST, plan, HISTORY))


def test_existing_budget_cap_is_enforced_during_the_edit():
    plan = with_church()
    plan["budget_lkr"] = 30001
    retry = MagicMock(return_value=with_church())
    result = validate_ai_response(REQUEST, plan, retry, history=HISTORY)
    retry.assert_called_once()
    assert result["budget_lkr"] == 30000


def test_unchanged_plan_is_corrected_in_one_retry():
    retry = MagicMock(return_value=with_church())
    result = validate_ai_response(REQUEST, copy.deepcopy(PLAN), retry, history=HISTORY)
    retry.assert_called_once()
    assert result["itinerary"][0]["items"][-1].startswith("Visit Dutch Reformed Church")


def test_failed_retry_does_not_present_an_unchanged_plan_as_success():
    result = validate_ai_response(REQUEST, copy.deepcopy(PLAN), lambda _: copy.deepcopy(PLAN), history=HISTORY)
    assert result["itinerary"] == []
    assert "consistent itinerary" in result["summary"]
    assert result["budget_cap_lkr"] == 30000


def test_other_edits_and_missing_history_do_not_trigger_addition_checks():
    assert validate_attraction_addition("Make day 2 relaxed", PLAN, HISTORY) == []
    assert validate_attraction_addition(REQUEST, PLAN, []) == []
    assert addition_prompt_context("Do not add one more cultural attraction", HISTORY) == ""


def test_prompt_proposes_sourced_places_that_were_not_previously_visited():
    context = addition_prompt_context(REQUEST, HISTORY)
    assert "Dutch Reformed Church" in context
    assert "Galle Maritime Museum" in context
    assert "https://www.srilanka.travel/" in context
    assert "- Brief Garden:" not in context
    assert "- Galle National Museum:" not in context


def test_stream_corrects_the_missing_addition_before_showing_the_plan(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = MagicMock()
    monkeypatch.setattr(groq_service, "Groq", lambda **_: client)
    monkeypatch.setattr(groq_service, "_build_prompt_messages", lambda *_, **__: ([], [], []))

    class Stream:
        closed = False

        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=json.dumps(PLAN)), finish_reason="stop",
            )])

        def close(self):
            self.closed = True

    stream = Stream()
    corrected = with_church()
    client.chat.completions.create.side_effect = [stream, SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(corrected)), finish_reason="stop",
    )])]

    events = list(groq_service.stream_groq_reply(REQUEST, history=HISTORY))

    assert stream.closed
    assert client.chat.completions.create.call_count == 2
    assert events[-1][0] == "done"
    reply = events[-1][1]
    assert validate_attraction_addition(REQUEST, reply, HISTORY) == []
    assert reply["budget_lkr"] == 30000
    assert reply["budget_breakdown"]["total_lkr"] == 30000
