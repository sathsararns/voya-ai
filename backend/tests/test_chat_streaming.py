"""Tests for streaming chat (POST /api/v1/chat/stream).

Two layers, matching this project's existing split in style:

- Unit tests for the pure pieces that make streaming possible at all —
  json_utils.SummaryStreamExtractor (decoding the visible `summary` text out
  of a raw, incomplete JSON fragment stream) and
  services.groq_service.stream_groq_reply's error fallback — no network, no
  DB, fully deterministic.
- Integration tests against the real FastAPI app (like
  tests/test_app_flows.py and tests/test_account_scoped_chats.py), proving
  the endpoint itself: SSE framing, that the exact reply streamed is the one
  saved, and that account scoping holds for this new code path exactly like
  it does for POST /.

Run from the backend folder:
    pytest tests/test_chat_streaming.py -v
"""

import uuid
from typing import List, Tuple

from fastapi.testclient import TestClient

from json_utils import SummaryStreamExtractor
from main import app
from services.groq_service import stream_groq_reply


# --- SummaryStreamExtractor (pure, no network) -------------------------------


class TestSummaryStreamExtractor:
    def test_decodes_a_simple_summary_delivered_in_one_chunk(self):
        extractor = SummaryStreamExtractor()
        out = extractor.feed('{"destination": "Kandy", "summary": "Here is a plan.", "days": 3}')
        assert out == "Here is a plan."

    def test_decodes_a_summary_split_across_many_arbitrary_chunk_boundaries(self):
        # Mirrors real Groq streaming: the JSON arrives as a sequence of
        # small, arbitrarily-sized token fragments with no respect for
        # field/string boundaries.
        raw = '{"summary": "Kandy is a lovely hill-country city.", "days": 2}'
        extractor = SummaryStreamExtractor()
        collected = ""
        for i in range(0, len(raw), 3):  # feed 3 characters at a time
            collected += extractor.feed(raw[i : i + 3])
        assert collected == "Kandy is a lovely hill-country city."

    def test_ignores_fields_before_and_after_summary(self):
        extractor = SummaryStreamExtractor()
        out = extractor.feed(
            '{"destination": "Galle", "days": 2, "budget_lkr": 50000, '
            '"summary": "A short trip.", "itinerary": [{"day": 1}]}'
        )
        assert out == "A short trip."

    def test_decodes_standard_json_escapes(self):
        extractor = SummaryStreamExtractor()
        out = extractor.feed(r'{"summary": "Line one\nLine two \"quoted\" and a backslash \\ here."}')
        assert out == 'Line one\nLine two "quoted" and a backslash \\ here.'

    def test_decodes_a_unicode_escape_split_across_feed_calls(self):
        # ’ is a right single quotation mark (').
        extractor = SummaryStreamExtractor()
        collected = ""
        for fragment in ['{"summary": "It', "\\u201", '9s lovely."}']:
            collected += extractor.feed(fragment)
        assert collected == "It’s lovely."

    def test_stops_after_the_closing_quote_even_if_more_json_follows(self):
        extractor = SummaryStreamExtractor()
        collected = extractor.feed('{"summary": "Done."} trailing garbage "summary" more text')
        assert collected == "Done."

    def test_no_summary_key_yields_nothing(self):
        extractor = SummaryStreamExtractor()
        assert extractor.feed('{"destination": "Ella", "days": 1}') == ""


# --- stream_groq_reply error fallback (pure, no network) ---------------------


class TestStreamGroqReplyFallback:
    def test_missing_api_key_yields_a_single_done_event_with_a_safe_fallback(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        events: List[Tuple[str, object]] = list(stream_groq_reply("Plan a trip to Kandy"))

        assert len(events) == 1
        kind, payload = events[0]
        assert kind == "done"
        assert isinstance(payload, dict)
        assert "GROQ_API_KEY is missing" in payload["summary"]
        assert payload["itinerary"] == []
        assert payload["kb_sources"] == []


# --- endpoint integration tests (real app, real Groq, real Postgres) --------


def _parse_sse(raw_text: str) -> List[Tuple[str, str]]:
    """Turn a raw `text/event-stream` body into a list of (event, data)
    pairs, in arrival order. Mirrors exactly what the frontend's SSE parser
    (see frontend/src/lib/api.ts's streamChat) does with the same bytes.
    """
    events: List[Tuple[str, str]] = []
    for block in raw_text.split("\n\n"):
        if not block.strip():
            continue
        event_type = "message"
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if data_lines:
            events.append((event_type, "\n".join(data_lines)))
    return events


def _signup(client: TestClient, name: str) -> dict:
    email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": name, "email": email, "password": "Password123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _stream_chat(client: TestClient, message: str, conversation_id: str | None = None) -> dict:
    """POST /api/v1/chat/stream and return the parsed result:
    {"tokens": [str, ...], "done": <parsed dict from the done event>}.
    """
    import json as _json

    response = client.post(
        "/api/v1/chat/stream",
        json={"message": message, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert events, "expected at least one SSE event"

    tokens = [_json.loads(data)["text"] for kind, data in events if kind == "token"]
    done_events = [_json.loads(data) for kind, data in events if kind == "done"]
    error_events = [_json.loads(data) for kind, data in events if kind == "error"]

    assert not error_events, f"stream reported an error: {error_events}"
    assert len(done_events) == 1, "expected exactly one terminal done event"

    return {"tokens": tokens, "done": done_events[0]}


class TestChatStreamEndpoint:
    def test_stream_produces_token_events_and_one_terminal_done_event(self):
        client = TestClient(app)
        _signup(client, "Stream")

        result = _stream_chat(client, "Plan a 2 day trip to Kandy")

        assert result["done"]["summary"]
        assert result["done"]["conversation_id"]
        # The whole point of streaming: the visible text arrives in pieces,
        # not as a single blob only once everything is ready.
        assert len(result["tokens"]) >= 1

    def test_final_saved_message_matches_the_done_events_summary_exactly(self):
        """Requirement: streaming must not change what ends up persisted —
        the conversation history's stored reply must be byte-for-byte the
        same summary the client's terminal `done` event carried, regardless
        of how the text arrived along the way.
        """
        client = TestClient(app)
        _signup(client, "Stream")

        result = _stream_chat(client, "Plan a 1 day trip to Galle")
        conversation_id = result["done"]["conversation_id"]

        history = client.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()
        assert len(history["messages"]) == 1
        saved_reply = history["messages"][0]["assistant_reply"]
        assert saved_reply["summary"] == result["done"]["summary"]
        assert saved_reply["destination"] == result["done"]["destination"]

    def test_followup_stream_message_stays_in_the_same_conversation(self):
        client = TestClient(app)
        _signup(client, "Stream")

        first = _stream_chat(client, "Plan a 2 day trip to Galle")
        conversation_id = first["done"]["conversation_id"]

        second = _stream_chat(client, "Make day 2 more relaxed", conversation_id=conversation_id)
        assert second["done"]["conversation_id"] == conversation_id

        history = client.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()
        assert len(history["messages"]) == 2

    def test_streamed_conversation_is_account_scoped_like_the_non_streaming_endpoint(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        _signup(client_a, "StreamOwner")
        _signup(client_b, "StreamOther")

        result = _stream_chat(client_a, "Plan a trip to Sigiriya via streaming")
        conversation_id = result["done"]["conversation_id"]

        # Owner sees it in Recent Chats.
        owner_conversations = client_a.get("/api/v1/chat/conversations").json()["conversations"]
        assert any(c["conversation_id"] == conversation_id for c in owner_conversations)

        # A different account never does, and can't read it by id either —
        # identical guarantee to POST / (see test_account_scoped_chats.py).
        other_conversations = client_b.get("/api/v1/chat/conversations").json()["conversations"]
        assert all(c["conversation_id"] != conversation_id for c in other_conversations)
        assert client_b.get(f"/api/v1/chat/conversations/{conversation_id}/messages").status_code == 404

    def test_anonymous_streaming_still_works_and_is_never_owned_by_an_account(self):
        anon_client = TestClient(app)
        session_id = f"pytest-stream-anon-{uuid.uuid4()}"

        response = anon_client.post(
            "/api/v1/chat/stream",
            json={"message": "Anonymous streamed message", "session_id": session_id},
        )
        assert response.status_code == 200
        events = _parse_sse(response.text)
        done_events = [e for e in events if e[0] == "done"]
        assert len(done_events) == 1

        conversations = anon_client.get(
            "/api/v1/chat/conversations", params={"session_id": session_id}
        ).json()["conversations"]
        assert len(conversations) == 1

    def test_a_broken_session_cookie_is_rejected_before_any_streaming_starts(self):
        """Same auth guarantee as POST / (see routes/auth.py's
        get_current_user_optional and test_account_scoped_chats.py) — proves
        adding the streaming endpoint didn't bypass it. FastAPI resolves
        dependencies before the endpoint body runs, so a rejected caller
        never even starts receiving SSE bytes; they just get a normal 401.
        """
        client = TestClient(app)
        _signup(client, "Stream")

        client.cookies.set("voya_access_token", "not-a-real-token")
        response = client.post(
            "/api/v1/chat/stream",
            json={"message": "Should be rejected"},
            headers={"X-Client-Expects-Auth": "1"},
        )
        assert response.status_code == 401
