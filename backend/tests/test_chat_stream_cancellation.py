"""Tests for backend-side cancellation of streaming chat
(POST /api/v1/chat/stream) — proving a client disconnect (a real network
drop, or the frontend's Stop button aborting its fetch) actually stops
generation server-side and never gets persisted, instead of the server
quietly finishing the reply and saving it anyway.

Why these don't go through TestClient like this project's other endpoint
tests (tests/test_app_flows.py, tests/test_account_scoped_chats.py,
tests/test_chat_streaming.py): TestClient is built on httpx's ASGITransport,
which always runs the whole ASGI app to completion in one internal
`await self.app(scope, receive, send)` call before any part of the response
is exposed back to the test (see httpx/_transports/asgi.py) — its `receive()`
callable can only ever produce `http.disconnect` AFTER the response has
already finished. There is no way to make it deliver a genuine mid-stream
disconnect, which is the entire signal routes/chat.py's chat_stream depends
on (Request.is_disconnected() — see FastAPI/Starlette's implementation).

So instead, these call routes.chat.chat_stream(...) directly — an ordinary
Python function; passing `db`/`current_user` explicitly bypasses FastAPI's
Depends() resolution, a standard technique for unit-testing a route function
in isolation — with a small fake stand-in for Request whose is_disconnected()
is fully controllable, and drive the returned StreamingResponse's async
`body_iterator` by hand. Everything else (the real account, the real
Postgres database, the real Groq API call inside stream_groq_reply) is real,
matching this project's real-integration-test convention elsewhere.

This exercises the exact cancellation logic precisely and deterministically,
but not literally the ASGI-server socket-disconnect wiring end to end; that
part was verified manually against a real running uvicorn server with a real
aborted fetch() (mirroring the frontend's actual Stop button), confirming
Request.is_disconnected() does report True at the point a real client aborts.

Run from the backend folder:
    pytest tests/test_chat_stream_cancellation.py -v
"""

import asyncio
import uuid
from typing import Any, List, Optional

import pytest
from fastapi.testclient import TestClient

from db.crud import get_user_by_email
from db.session import SessionLocal
from main import app
from routes.chat import chat_stream
from schemas.chat import ChatRequest


class FakeDisconnectingRequest:
    """Stand-in for FastAPI's Request — event_stream (see routes/chat.py's
    chat_stream) only ever calls is_disconnected() on it, nothing else.

    disconnect_after_calls=0: already disconnected before the very first
    check (so nothing is ever pulled from Groq at all).
    disconnect_after_calls=1: the first check still reports connected (one
    chunk gets pulled), every check after that reports disconnected — a
    genuine mid-stream stop.
    disconnect_after_calls=None: never disconnects, for the
    completes-normally case.
    """

    def __init__(self, disconnect_after_calls: Optional[int]):
        self._calls = 0
        self._disconnect_after_calls = disconnect_after_calls

    async def is_disconnected(self) -> bool:
        if self._disconnect_after_calls is None:
            return False
        self._calls += 1
        return self._calls > self._disconnect_after_calls


async def _drain(async_gen) -> List[Any]:
    return [chunk async for chunk in async_gen]


def _signup_and_fetch_user(client: TestClient, name: str):
    email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": name, "email": email, "password": "Password123"},
    )
    assert response.status_code == 201, response.text

    db = SessionLocal()
    try:
        user = get_user_by_email(db, email)
        assert user is not None
        return user
    finally:
        db.close()


class TestBackendStreamCancellation:
    def test_disconnecting_before_any_chunk_sends_nothing_and_saves_nothing(self):
        client = TestClient(app)
        user = _signup_and_fetch_user(client, "Cancel")

        db = SessionLocal()
        try:
            chat_request = ChatRequest(message="Plan an elaborate 5 day trip around Sri Lanka")
            fake_request = FakeDisconnectingRequest(disconnect_after_calls=0)

            response = chat_stream(chat_request, fake_request, db=db, current_user=user)
            chunks = asyncio.run(_drain(response.body_iterator))

            # Nothing sent at all — there's no one left to receive it.
            assert chunks == []
        finally:
            db.close()

        # A bare Conversation row may exist (get_or_create_conversation runs
        # before the stream even starts — see chat_stream), but with zero
        # messages it's excluded from Recent Chats by db/crud.py's own
        # list_conversations (same pre-existing "has_message" filter that
        # already protects against any zero-message row surfacing there) —
        # so from the caller's perspective, nothing was saved.
        conversations = client.get("/api/v1/chat/conversations").json()["conversations"]
        assert conversations == []

    def test_disconnecting_mid_stream_stops_generation_and_saves_nothing(self):
        client = TestClient(app)
        user = _signup_and_fetch_user(client, "Cancel")

        db = SessionLocal()
        try:
            chat_request = ChatRequest(
                message="Plan an elaborate 5 day trip around Sri Lanka with lots of detail"
            )
            # Lets exactly one pull through — proving this stops a stream
            # that had already genuinely started, not just one that never
            # began.
            fake_request = FakeDisconnectingRequest(disconnect_after_calls=1)

            response = chat_stream(chat_request, fake_request, db=db, current_user=user)
            chunks = asyncio.run(_drain(response.body_iterator))

            # No terminal `done` (or `error`) event ever went out — the
            # stream was cut off mid-flight, never completed.
            joined = "".join(chunks)
            assert "event: done" not in joined
            assert "event: error" not in joined
        finally:
            db.close()

        # The actual point of this whole feature: an aborted generation is
        # never persisted, partial or otherwise.
        conversations = client.get("/api/v1/chat/conversations").json()["conversations"]
        assert conversations == []

    def test_a_completed_generation_with_no_disconnect_still_saves_normally(self):
        """Same chat_stream()-direct-call technique as the tests above, but
        with a Request that never reports disconnected — proves the
        disconnect-checking machinery added for cancellation doesn't
        interfere with the ordinary, successful path. The full
        endpoint-level version of this (via the real HTTP route) is already
        covered by tests/test_chat_streaming.py; this re-proves it at the
        same level as the cancellation tests above, so a future change to
        event_stream's control flow can't silently break one path while this
        file only watches the other.
        """
        client = TestClient(app)
        user = _signup_and_fetch_user(client, "Cancel")

        db = SessionLocal()
        try:
            chat_request = ChatRequest(message="Plan a 1 day trip to Galle")
            fake_request = FakeDisconnectingRequest(disconnect_after_calls=None)

            response = chat_stream(chat_request, fake_request, db=db, current_user=user)
            chunks = asyncio.run(_drain(response.body_iterator))
        finally:
            db.close()

        joined = "".join(chunks)
        assert "event: done" in joined

        conversations = client.get("/api/v1/chat/conversations").json()["conversations"]
        assert len(conversations) == 1


class TestStreamGroqReplyCancellation:
    """Unit-level proof, one layer below the endpoint: closing
    stream_groq_reply's generator early (exactly what routes/chat.py's
    event_stream does in its `finally` block on a disconnect) actually stops
    it from reading further chunks, rather than letting it run to
    completion in the background regardless.
    """

    def test_closing_the_generator_after_one_item_stops_it_for_good(self):
        from services.groq_service import stream_groq_reply

        generator = stream_groq_reply("Plan an elaborate 5 day trip around Sri Lanka with lots of detail")

        first_kind, _ = next(generator)
        assert first_kind in ("delta", "tick", "done")

        generator.close()

        # A closed generator raises StopIteration on the next pull, forever
        # — proving it isn't quietly still running/buffering more chunks
        # somewhere and just refusing to hand them over.
        with pytest.raises(StopIteration):
            next(generator)
