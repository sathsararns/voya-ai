"""Both chat transports derive memory ownership from authentication."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pinecone_memory
from routes import chat as routes
from schemas.chat import ChatRequest


@pytest.mark.parametrize("streaming", [False, True])
def test_switching_accounts_in_one_browser_isolates_memory(monkeypatch, streaming):
    monkeypatch.setattr(routes, "get_or_create_conversation", lambda *_a, **_k: SimpleNamespace(conversation_id="test"))
    monkeypatch.setattr(routes, "count_conversation_messages", lambda *_: 0)
    monkeypatch.setattr(routes, "save_chat", MagicMock())
    monkeypatch.setattr(routes, "touch_conversation", MagicMock())
    monkeypatch.setattr(routes, "_learn_preferences", MagicMock())
    monkeypatch.setattr(routes, "build_memory_text", lambda *_: "Synthetic preference")
    saved = MagicMock()
    monkeypatch.setattr(routes, "save_memory", saved)
    captured = []
    def generate(message, scope, **kwargs):
        captured.append((scope, kwargs["use_memory"]))
        return {"summary": "Synthetic reply", "itinerary": []}
    def stream(*args, **kwargs):
        yield "done", generate(*args, **kwargs)
    monkeypatch.setattr(routes, "get_groq_reply", generate)
    monkeypatch.setattr(routes, "stream_groq_reply", stream)
    class Connected:
        async def is_disconnected(self):
            return False
    request = ChatRequest(message="Plan a trip to Kandy", session_id="same-browser")
    for user in [SimpleNamespace(id=11, preferences={}), SimpleNamespace(id=22, preferences={}), None]:
        if streaming:
            response = routes.chat_stream(request, Connected(), db=object(), current_user=user)
            async def consume():
                return "".join([part async for part in response.body_iterator])
            assert "event: done" in asyncio.run(consume())
        else:
            routes.chat(request, db=object(), current_user=user)
    assert captured[0] == (pinecone_memory.memory_scope_for_user(11), True)
    assert captured[1] == (pinecone_memory.memory_scope_for_user(22), True)
    assert captured[0][0] != captured[1][0]
    assert captured[2] == (None, False)
    assert saved.call_count == 2
    assert [c.kwargs["session_id"] for c in saved.call_args_list] == [c[0] for c in captured[:2]]


def test_anonymous_memory_never_uses_shared_fallback(monkeypatch):
    index = MagicMock()
    monkeypatch.setattr(pinecone_memory, "index", index)
    pinecone_memory.save_memory(None, "test", "Synthetic text")
    assert pinecone_memory.search_memory(None, "test") == []
    index.upsert_records.assert_not_called()
    index.search.assert_not_called()
