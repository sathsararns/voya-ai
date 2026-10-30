"""Regression coverage for option validation and provider output recovery.

All completions are stubbed; these tests do not call external services.
"""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from groq import BadRequestError

from json_utils import normalize_chat_response
from services import groq_service
from services.context_router import decide_context_strategy, is_options_request
from services.response_validator import matches_request, validate_ai_response, validate_itinerary


OPTIONS_REQUEST = """Give me these four numbered travel options. Briefly describe
each one without creating a detailed itinerary yet:
1. A 2-day cultural trip to Kandy.
2. A 5-day coastal tour of Galle, Mirissa and Bentota.
3. A 3-day northern trip to Jaffna.
4. A 4-day nature trip to Ella.
Then ask me to choose an option."""

PLAN = {
    "destination": "Galle, Mirissa and Bentota",
    "days": 5,
    "budget_lkr": 45000,
    "summary": "A coastal trip with an estimated total of LKR 45,000.",
    "itinerary": [{"day": 1, "title": "Arrival", "items": ["Check in", "Explore Galle Fort"]}],
    "follow_up_question": None,
}


@pytest.mark.parametrize("message", [OPTIONS_REQUEST, "give options", "Show me some options"])
def test_options_are_not_validated_as_one_trip(message):
    options = {
        "summary": "1) Kandy for 2 days. 2) Galle, Mirissa and Bentota for 5 days. "
                   "3) Jaffna for 3 days. 4) Ella for 4 days.",
        "itinerary": [],
        "follow_up_question": "Which option would you like?",
    }
    retry = MagicMock(side_effect=AssertionError("A valid options reply must not need correction"))

    result = validate_ai_response(message, options, retry)

    assert result == options
    retry.assert_not_called()


def test_options_request_does_not_offer_the_detailed_itinerary_tool():
    assert "generate_itinerary" not in decide_context_strategy(OPTIONS_REQUEST, 0).candidate_tools


def test_food_options_are_still_an_itinerary_edit():
    assert not is_options_request("Make the second day more relaxed and include vegetarian food options.")
    assert matches_request("Plan a 2-day trip to Galle", PLAN)


def test_detailed_plan_is_rejected_when_only_alternatives_were_requested():
    assert matches_request(OPTIONS_REQUEST, PLAN)


@pytest.mark.parametrize("bad_item", [
    'Check in at a budget guesthouse"',
    'Visit the temple","Leisure time for a beach walk","Check out',
])
def test_json_artifacts_trigger_a_corrected_reply(bad_item):
    malformed = copy.deepcopy(PLAN)
    malformed["itinerary"][0]["items"] = [bad_item]
    retry = MagicMock(return_value=copy.deepcopy(PLAN))

    result = validate_ai_response("Update the trip", malformed, retry)

    retry.assert_called_once()
    assert result == PLAN


def test_unrepaired_json_artifacts_are_never_rendered_as_activities():
    malformed = copy.deepcopy(PLAN)
    malformed["itinerary"][0]["items"] = ['Check in"']
    result = validate_ai_response("Update the trip", malformed, lambda _: malformed)

    assert result["itinerary"] == []


def test_normal_quoted_place_names_are_preserved():
    plan = copy.deepcopy(PLAN)
    plan["itinerary"][0]["items"] = ['Visit the "Tea Museum"']
    assert validate_itinerary(plan) == []


def test_serialized_activity_list_is_decoded_into_separate_bullets():
    raw = copy.deepcopy(PLAN)
    raw["itinerary"][0]["items"] = json.dumps(["Visit the temple", "Relax on the beach"])

    result = normalize_chat_response(json.dumps(raw))

    assert result["itinerary"][0]["items"] == ["Visit the temple", "Relax on the beach"]


def completion(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason,
    )])


class FakeStream:
    def __init__(self, content, finish_reason="stop"):
        self.content = content
        self.finish_reason = finish_reason
        self.closed = False

    def __iter__(self):
        yield SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(content=self.content), finish_reason=self.finish_reason,
        )])

    def close(self):
        self.closed = True


def provider_error(code="tool_use_failed", message="Tool choice is none, but model called a tool"):
    return BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com/test")),
        body={"error": {"code": code, "message": message}},
    )


@pytest.fixture
def fake_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = MagicMock()
    monkeypatch.setattr(groq_service, "Groq", lambda **_: client)
    monkeypatch.setattr(groq_service, "_build_prompt_messages", lambda *_, **__: (
        [{"role": "user", "content": "Update the budget"}], [], ["calculate_budget"],
    ))
    return client


def test_stream_recovers_from_unexpected_tool_call_with_strict_json(fake_provider):
    fake_provider.chat.completions.create.side_effect = [
        provider_error(), completion(json.dumps(PLAN)),
    ]

    events = list(groq_service.stream_groq_reply("My total budget is LKR 45000"))

    assert [kind for kind, _ in events] == ["delta", "done"]
    assert events[-1][1]["budget_lkr"] == 45000
    assert events[-1][1]["destination"] == PLAN["destination"]
    assert events[-1][1]["tools_used"] == ["calculate_budget"]
    calls = fake_provider.chat.completions.create.call_args_list
    assert calls[0].kwargs["stream"] is True
    assert calls[0].kwargs["tool_choice"] == "none"
    assert calls[1].kwargs["response_format"]["json_schema"]["strict"] is True
    assert not calls[1].kwargs.get("stream", False)


@pytest.mark.parametrize("content,finish_reason", [
    ('{"summary":"A coastal trip", "itinerary":[', "length"),
    ('{"summary":"A coastal trip", "itinerary":[', "stop"),
])
def test_incomplete_stream_is_regenerated_instead_of_silently_repaired(fake_provider, content, finish_reason):
    stream = FakeStream(content, finish_reason)
    fake_provider.chat.completions.create.side_effect = [stream, completion(json.dumps(PLAN))]

    events = list(groq_service.stream_groq_reply("Update the trip"))

    assert stream.closed
    assert events[-1][0] == "done"
    assert events[-1][1]["itinerary"] == PLAN["itinerary"]
    assert fake_provider.chat.completions.create.call_count == 2


def test_nonstreamed_generation_retries_an_output_limit(fake_provider):
    fake_provider.chat.completions.create.side_effect = [
        completion('{"summary":', "length"), completion(json.dumps(PLAN)),
    ]

    result = groq_service.get_groq_reply("My total budget is LKR 45000")

    assert result["budget_lkr"] == 45000
    calls = fake_provider.chat.completions.create.call_args_list
    assert calls[1].kwargs["max_completion_tokens"] > calls[0].kwargs["max_completion_tokens"]


def test_recovery_is_bounded_and_does_not_expose_provider_errors(fake_provider):
    fake_provider.chat.completions.create.side_effect = provider_error()

    events = list(groq_service.stream_groq_reply("Update the budget"))

    assert fake_provider.chat.completions.create.call_count == 3
    assert events[-1][1]["summary"] == groq_service.GENERATION_FAILURE_MESSAGE
    assert "tool" not in events[-1][1]["summary"].lower()
    assert events[-1][1]["itinerary"] == []


def test_unrelated_provider_errors_are_not_retried(fake_provider):
    fake_provider.chat.completions.create.side_effect = provider_error(
        "invalid_api_key", "Credential rejected",
    )

    result = groq_service.get_groq_reply("Update the trip")

    assert fake_provider.chat.completions.create.call_count == 1
    assert result["summary"] == groq_service.GENERATION_FAILURE_MESSAGE


def test_invalid_budget_summary_is_never_streamed_before_its_correction(fake_provider):
    bad = {**PLAN, "budget_lkr": 50000, "summary": "Spend LKR 50,000 on this trip."}
    stream = FakeStream(json.dumps(bad))
    fake_provider.chat.completions.create.side_effect = [stream, completion(json.dumps(PLAN))]

    events = list(groq_service.stream_groq_reply("My budget is LKR 45000"))

    visible = [payload for kind, payload in events if kind == "delta"]
    assert visible == [PLAN["summary"]]
    assert events[-1][1]["budget_lkr"] == 45000
    assert events[-1][1]["budget_breakdown"]["total_lkr"] == 45000
    assert stream.closed


def test_cancelling_during_validation_buffer_closes_stream_without_a_reply(fake_provider):
    stream = FakeStream(json.dumps(PLAN))
    fake_provider.chat.completions.create.return_value = stream
    generator = groq_service.stream_groq_reply("My budget is LKR 45000")

    assert next(generator) == ("tick", None)
    generator.close()

    assert stream.closed
    assert fake_provider.chat.completions.create.call_count == 1
