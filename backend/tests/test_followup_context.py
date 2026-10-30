"""Check context delivery through both chat routes without external calls.

DB reads/writes and model generation are replaced at their boundaries;
the real router, history formatting, SSE response, and prompt builder run.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from routes import chat as chat_routes
from schemas.chat import ChatRequest
from services import groq_service
from services.history_manager import ConversationContext


OPTIONS = (
    "1) A scenic Hill Country trip through Kandy, Nuwara Eliya and Ella. "
    "2) A cultural coastal tour of Galle, Mirissa and Bentota. "
    "3) A northern adventure to Jaffna."
)
HISTORY = [
    {"role": "user", "content": "give options"},
    {"role": "assistant", "content": json.dumps({"summary": OPTIONS, "itinerary": []})},
]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("message", ["i choose 2", "2", "option 2", "the second one"])
def test_chat_routes_send_the_offered_options_to_generation(monkeypatch, streaming, message):
    db = object()
    context = ConversationContext(
        summary=None,
        recent_messages=[SimpleNamespace(
            user_message=HISTORY[0]["content"], assistant_reply=HISTORY[1]["content"],
        )],
        total_messages=1,
        summarized=False,
    )
    load_context = MagicMock(return_value=context)
    monkeypatch.setattr(chat_routes, "get_or_create_conversation", MagicMock(
        return_value=SimpleNamespace(conversation_id="option-conversation"),
    ))
    monkeypatch.setattr(chat_routes, "count_conversation_messages", lambda *_: 1)
    monkeypatch.setattr(chat_routes, "build_context", load_context)
    save_chat = MagicMock()
    monkeypatch.setattr(chat_routes, "save_chat", save_chat)
    monkeypatch.setattr(chat_routes, "touch_conversation", MagicMock())
    monkeypatch.setattr(chat_routes, "build_memory_text", lambda *_: "")
    reply = {
        "summary": "You chose the coastal tour of Galle, Mirissa and Bentota.",
        "itinerary": [],
        "follow_up_question": "How many days would you like to travel?",
    }
    captured = {}

    def generate(user_message, session_id, **kwargs):
        captured.update(message=user_message, session_id=session_id, **kwargs)
        return reply

    def generate_stream(*args, **kwargs):
        result = generate(*args, **kwargs)
        yield "delta", result["summary"]
        yield "done", result

    monkeypatch.setattr(chat_routes, "get_groq_reply", generate)
    monkeypatch.setattr(chat_routes, "stream_groq_reply", generate_stream)
    request = ChatRequest(
        message=message, session_id="test-session", conversation_id="option-conversation",
    )

    if streaming:
        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        response = chat_routes.chat_stream(request, ConnectedRequest(), db=db, current_user=None)

        async def read_stream():
            return "".join([chunk async for chunk in response.body_iterator])

        assert "event: done" in asyncio.run(read_stream())
    else:
        assert chat_routes.chat(request, db=db, current_user=None)["summary"] == reply["summary"]

    load_context.assert_called_once_with(db, "option-conversation", prefer_summary=False)
    assert captured["message"] == message
    assert captured["history"] == HISTORY
    assert captured["use_memory"] is False
    save_chat.assert_called_once()


def test_option_weather_keeps_choices_without_guessing_a_saved_destination(monkeypatch):
    model = MagicMock(side_effect=AssertionError("Prompt preparation must not spend an AI call"))
    monkeypatch.setattr(groq_service, "Groq", model)
    message = "What is the weather for option 2?"

    messages, sources, tools_used = groq_service._build_prompt_messages(
        message, "test-session", HISTORY,
        use_memory=False, use_knowledge_base=False, api_key="test-key",
        candidate_tools=["get_weather"],
        user_preferences={"preferred_destinations": ["Kandy"]},
    )

    assert messages[1] == HISTORY[0]
    assert json.loads(messages[2]["content"]) == json.loads(HISTORY[1]["content"])
    assert messages[-1] == {"role": "user", "content": message}
    model.assert_not_called()
    assert sources == []
    assert tools_used == []
