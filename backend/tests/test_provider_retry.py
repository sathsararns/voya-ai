"""Transient provider failures recover without guessing or saving error replies."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from groq import APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from routes import chat as routes
from schemas.chat import ChatRequest, ChatResponse
from services import groq_service, provider_retry


def error_of(error_type=RateLimitError, status=429, headers=None):
    request = httpx.Request("POST", "https://api.groq.com/test")
    return error_type("private provider diagnostics", response=httpx.Response(
        status, request=request, headers=headers or {}), body={"error": "private provider diagnostics"})


@pytest.mark.parametrize("headers,expected", [
    ({"retry-after": "0.5"}, 0.5),
    ({"retry-after-ms": "250", "retry-after": "3"}, 0.25),
    ({"retry-after-ms": "invalid", "retry-after": "3"}, 3),
    ({"retry-after": "0"}, 0),
    ({"retry-after": "NaN"}, None),
    ({"retry-after": "-5"}, None),
    ({}, None),
])
def test_retry_delay_parses_provider_headers_safely(headers, expected):
    assert provider_retry.retry_after_seconds(error_of(headers=headers)) == expected


def test_short_rate_limit_waits_and_recovers_without_leaking_diagnostics(monkeypatch, caplog):
    client = MagicMock()
    client.chat.completions.create.side_effect = [error_of(headers={"retry-after": "0.5"}), "complete"]
    sleep = MagicMock()
    monkeypatch.setattr(provider_retry.time, "sleep", sleep)
    assert provider_retry.request_completion(client, model="test") == "complete"
    sleep.assert_called_once_with(0.5)
    assert client.chat.completions.create.call_count == 2
    assert "private provider diagnostics" not in caplog.text
    assert "RateLimitError" in caplog.text


def test_server_error_recovers_once(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.side_effect = [error_of(APIStatusError, 503), "complete"]
    monkeypatch.setattr(provider_retry.time, "sleep", MagicMock())
    assert provider_retry.request_completion(client) == "complete"
    assert client.chat.completions.create.call_count == 2


def test_long_cooldown_is_not_retried_early(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.side_effect = error_of(headers={"retry-after": "90"})
    sleep = MagicMock()
    monkeypatch.setattr(provider_retry.time, "sleep", sleep)
    with pytest.raises(RateLimitError):
        provider_retry.request_completion(client)
    assert client.chat.completions.create.call_count == 1
    sleep.assert_not_called()


def test_credentials_are_not_retried(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.side_effect = error_of(AuthenticationError, 401)
    sleep = MagicMock()
    monkeypatch.setattr(provider_retry.time, "sleep", sleep)
    with pytest.raises(AuthenticationError):
        provider_retry.request_completion(client)
    sleep.assert_not_called()
    assert client.chat.completions.create.call_count == 1


def test_stream_cooldown_can_be_cancelled_before_retry(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.side_effect = error_of(headers={"retry-after": "1"})
    sleep = MagicMock()
    monkeypatch.setattr(provider_retry.time, "sleep", sleep)
    generator = provider_retry.open_completion_stream(client, stream=True)
    assert next(generator) == ("tick", None)
    generator.close()
    sleep.assert_not_called()
    assert client.chat.completions.create.call_count == 1


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    client = MagicMock()
    monkeypatch.setattr(groq_service, "Groq", lambda **_: client)
    monkeypatch.setattr(groq_service, "_build_prompt_messages", lambda *_a, **_k: (
        [{"role": "user", "content": "Plan a trip"}], [], []))
    monkeypatch.setattr(provider_retry.time, "sleep", MagicMock())
    return client


@pytest.mark.parametrize("streaming", [False, True])
def test_exhausted_rate_limit_has_specific_safe_message_without_duplicate_followup(provider, streaming):
    provider.chat.completions.create.side_effect = error_of(headers={"retry-after": "5"})
    if streaming:
        events = list(groq_service.stream_groq_reply("Plan a trip"))
        reply = next(data for event, data in events if event == "done")
    else:
        reply = groq_service.get_groq_reply("Plan a trip")
    assert provider.chat.completions.create.call_count == 2
    assert reply["error_code"] == "rate_limited"
    assert reply["retry_after_seconds"] == 5
    assert "5 seconds" in reply["summary"]
    assert reply["itinerary"] == [] and reply["follow_up_question"] is None
    assert "private" not in json.dumps(reply)
    assert ChatResponse.model_validate(reply).error_code == "rate_limited"


@pytest.mark.parametrize("streaming", [False, True])
def test_rate_limit_recovers_a_valid_saved_preference_itinerary(provider, streaming):
    plan = {"destination": "Kandy", "days": 2, "budget_lkr": None,
            "summary": "Two days in Kandy with vegetarian food and train travel.",
            "itinerary": [{"day": n, "title": "Kandy", "items": ["Vegetarian lunch", "Train travel"]}
                          for n in (1, 2)], "follow_up_question": None}
    class Stream:
        closed = False
        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=json.dumps(plan)), finish_reason="stop")])
        def close(self):
            self.closed = True
    stream = Stream()
    success = stream if streaming else SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=json.dumps(plan)), finish_reason="stop")])
    provider.chat.completions.create.side_effect = [error_of(headers={"retry-after": "0.5"}), success]
    query = "Plan a 2-day trip to Kandy using my saved food and transport preferences."
    kwargs = {"user_preferences": {"food_preferences": ["Vegetarian"], "transport_preference": "Train"}}
    if streaming:
        events = list(groq_service.stream_groq_reply(query, **kwargs))
        reply = next(data for event, data in events if event == "done")
        assert stream.closed
    else:
        reply = groq_service.get_groq_reply(query, **kwargs)
    assert reply.get("error_code") is None
    assert reply["days"] == 2 and len(reply["itinerary"]) == 2
    assert provider.chat.completions.create.call_count == 2


def test_timeout_message_is_distinct_from_invalid_generation(provider):
    provider.chat.completions.create.side_effect = APITimeoutError(
        request=httpx.Request("POST", "https://api.groq.com/test"))
    reply = groq_service.get_groq_reply("Plan a trip")
    assert reply["error_code"] == "provider_timeout"
    assert "too long" in reply["summary"]
    assert provider.chat.completions.create.call_count == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("error_code", ["rate_limited", "validation_failed"])
def test_failed_generation_never_becomes_chat_history_or_saved_preferences(monkeypatch, streaming, error_code):
    monkeypatch.setattr(routes, "get_or_create_conversation", lambda *_a, **_k: SimpleNamespace(conversation_id="test"))
    monkeypatch.setattr(routes, "count_conversation_messages", lambda *_: 0)
    save = MagicMock()
    learn = MagicMock()
    memory = MagicMock()
    monkeypatch.setattr(routes, "save_chat", save)
    monkeypatch.setattr(routes, "_learn_preferences", learn)
    monkeypatch.setattr(routes, "save_memory", memory)
    reply = groq_service._fallback_reply("Unable to complete.", error_code, 5)
    monkeypatch.setattr(routes, "get_groq_reply", lambda *_a, **_k: reply)
    def stream(*args, **kwargs):
        yield "done", reply
    monkeypatch.setattr(routes, "stream_groq_reply", stream)
    request = ChatRequest(message="Plan a trip to Kandy", session_id="test")
    user = SimpleNamespace(id=123, preferences={})
    if streaming:
        class Connected:
            async def is_disconnected(self):
                return False
        response = routes.chat_stream(request, Connected(), db=object(), current_user=user)
        async def consume():
            return "".join([chunk async for chunk in response.body_iterator])
        assert error_code in asyncio.run(consume())
    else:
        assert routes.chat(request, db=object(), current_user=user)["error_code"] == error_code
    save.assert_not_called()
    learn.assert_not_called()
    memory.assert_not_called()
