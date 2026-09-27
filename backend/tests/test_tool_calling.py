"""Tests for tool-calling: weather, places, budget, itinerary
(services/tools.py, plus the routing/orchestration around them in
services/context_router.py and services/groq_service.py).

Three layers, matching this project's existing split in style:
  - Pure unit tests for the tool functions themselves (deterministic, no
    network for get_weather/calculate_budget; real KB calls for
    search_places/generate_itinerary — consistent with how this project
    tests kb_rag.py elsewhere, see tests/test_app_flows.py).
  - Unit tests for context_router.py's candidate_tools gating — proving the
    model is only ever offered a relevant, scoped menu of tools, not all
    four for every message.
  - Integration tests against the real FastAPI app (real Groq, real
    Postgres) proving: the assistant can call a tool, a non-tool message is
    completely unaffected, streaming still works with tool-calling enabled,
    and account scoping is unchanged.

Run from the backend folder:
    pytest tests/test_tool_calling.py -v
"""

import json
import uuid
from typing import List, Tuple

from fastapi.testclient import TestClient

from main import app
from services.context_router import decide_context_strategy
from services.tools import calculate_budget, execute_tool_call, generate_itinerary, get_weather, search_places


# --- tool functions (pure, mostly no network) -------------------------------


class TestGetWeather:
    def test_returns_reference_climate_data_for_a_known_destination(self):
        result = get_weather("Kandy", days=3)
        assert result["destination"] == "Kandy"
        assert result["days"] == 3
        assert result["is_live_forecast"] is False
        assert result["region"] == "hill country"

    def test_is_case_insensitive_on_the_destination_name(self):
        assert get_weather("KANDY")["region"] == get_weather("kandy")["region"]

    def test_falls_back_to_generic_sri_lanka_climate_for_an_unknown_destination(self):
        result = get_weather("Nowhereville")
        assert result["is_live_forecast"] is False
        assert result["region"] == "Sri Lanka (general)"


class TestCalculateBudget:
    def test_splits_a_given_total_by_category_and_the_parts_sum_to_the_whole(self):
        result = calculate_budget(days=3, total_budget_lkr=30_000)
        assert result["total_budget_lkr"] == 30_000
        assert result["daily_budget_lkr"] == 10_000
        assert sum(result["breakdown_lkr"].values()) == 30_000

    def test_estimates_a_total_from_the_travel_style_when_none_is_given(self):
        result = calculate_budget(days=2, travel_style="luxury")
        assert result["travel_style"] == "luxury"
        assert result["total_budget_lkr"] == 45_000 * 2

    def test_falls_back_to_mid_range_for_an_unrecognized_style(self):
        result = calculate_budget(days=1, travel_style="ultra-mega-deluxe")
        assert result["travel_style"] == "mid-range"

    def test_clamps_an_unreasonable_day_count_instead_of_producing_a_nonsense_result(self):
        result = calculate_budget(days=9999)
        assert result["days"] == 60


class TestSearchPlacesAndGenerateItinerary:
    """Real KB (Pinecone) calls — same data source tests/test_app_flows.py's
    knowledge-base tests already depend on.
    """

    def test_search_places_returns_real_knowledge_base_matches(self):
        result = search_places("Kandy", limit=3)
        assert result["destination"] == "Kandy"
        assert len(result["places"]) > 0
        assert all(p["description"] for p in result["places"])

    def test_generate_itinerary_distributes_distinct_places_across_every_day(self):
        plan = generate_itinerary("Kandy", days=3)
        assert plan["days"] == 3
        assert len(plan["day_plan"]) == 3
        # The actual bug this guards against: KB chunks from the same
        # ingested document share one document_name (no per-chunk title),
        # so deduping on that display name instead of real chunk identity
        # collapsed every day after the first down to zero suggestions.
        assert all(len(day["suggested_places"]) > 0 for day in plan["day_plan"])


class TestExecuteToolCall:
    def test_returns_an_error_dict_for_an_unknown_tool_name_instead_of_raising(self):
        result = execute_tool_call("not_a_real_tool", {})
        assert "error" in result

    def test_returns_an_error_dict_instead_of_raising_on_missing_required_arguments(self):
        # calculate_budget requires `days` — omitting it raises a TypeError
        # inside the function itself, which execute_tool_call must catch.
        result = execute_tool_call("calculate_budget", {})
        assert "error" in result


# --- context_router.py's tool gating ----------------------------------------


class TestCandidateToolGating:
    def test_a_weather_question_offers_only_the_weather_tool(self):
        decision = decide_context_strategy("What's the weather like in Kandy?", 0)
        assert decision.candidate_tools == ["get_weather"]

    def test_a_budget_question_offers_only_the_budget_tool(self):
        # Deliberately avoids the substring "day trip" (see
        # ITINERARY_TOOL_KEYWORDS) — this is testing that a budget-only
        # question doesn't ALSO pull in the itinerary tool, not that the two
        # can never co-occur (test_a_message_can_offer_more_than_one_tool_at_once
        # below covers a message that legitimately should offer both).
        decision = decide_context_strategy("How much would a trip to Kandy cost?", 0)
        assert decision.candidate_tools == ["calculate_budget"]

    def test_a_places_question_offers_the_places_tool(self):
        decision = decide_context_strategy("What are some attractions in Galle?", 0)
        assert "search_places" in decision.candidate_tools

    def test_an_itinerary_request_offers_the_itinerary_tool(self):
        decision = decide_context_strategy("Plan a 4 day trip to Kandy", 0)
        assert "generate_itinerary" in decision.candidate_tools

    def test_a_message_can_offer_more_than_one_tool_at_once(self):
        decision = decide_context_strategy(
            "Plan a 3 day trip to Ella with a budget breakdown and what to pack", 0
        )
        assert "generate_itinerary" in decision.candidate_tools
        assert "calculate_budget" in decision.candidate_tools
        assert "get_weather" in decision.candidate_tools

    def test_a_greeting_offers_no_tools_at_all(self):
        decision = decide_context_strategy("hi", 0)
        assert decision.candidate_tools == []

    def test_a_message_with_no_tool_keywords_offers_no_tools(self):
        decision = decide_context_strategy("Tell me a fun fact.", 0)
        assert decision.candidate_tools == []


# --- end-to-end (real app, real Groq, real Postgres) ------------------------


def _signup(client: TestClient, name: str) -> dict:
    email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": name, "email": email, "password": "Password123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _parse_sse(raw_text: str) -> List[Tuple[str, str]]:
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


# A deliberately unambiguous request: the ONLY plausible tool for it (per
# context_router's own keyword gating, "budget"/"how much"/etc.) is
# calculate_budget, and it's exactly what calculate_budget does — chosen to
# make the model's tool_choice="auto" decision as close to certain as a real
# LLM call can be, while still being a genuine agentic decision, not a
# forced one (see groq_service.py's _run_tool_calling_phase: tool_choice is
# always "auto", never a specific forced tool).
BUDGET_TOOL_MESSAGE = "I have exactly 45000 LKR for a 3 day trip. Please calculate a detailed budget breakdown for me."


class TestToolCallingEndToEnd:
    def test_the_assistant_calls_a_tool_and_it_shows_up_in_tools_used(self):
        client = TestClient(app)
        _signup(client, "ToolUser")

        response = client.post("/api/v1/chat/", json={"message": BUDGET_TOOL_MESSAGE})
        assert response.status_code == 200, response.text
        data = response.json()

        assert "calculate_budget" in data["tools_used"]
        assert data["summary"]

    def test_a_plain_non_tool_message_is_completely_unaffected(self):
        client = TestClient(app)
        _signup(client, "ToolUser")

        response = client.post("/api/v1/chat/", json={"message": "Hi there!"})
        assert response.status_code == 200, response.text
        data = response.json()

        # No tool keywords in "Hi there!" -> context_router offers nothing
        # -> no extra Groq round trip happens at all (see
        # _run_tool_calling_phase's `if not candidate_tools: return "", []`).
        assert data["tools_used"] == []
        assert data["summary"]

    def test_streaming_still_works_with_tool_calling_enabled_and_the_save_matches_exactly(self):
        client = TestClient(app)
        _signup(client, "ToolUser")

        response = client.post(
            "/api/v1/chat/stream",
            json={
                "message": "I have exactly 20000 LKR for a 2 day trip. Please calculate a detailed budget breakdown."
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(response.text)
        token_events = [e for e in events if e[0] == "token"]
        done_events = [json.loads(data) for kind, data in events if kind == "done"]
        error_events = [e for e in events if e[0] == "error"]

        assert not error_events, f"stream reported an error: {error_events}"
        assert len(done_events) == 1
        assert len(token_events) >= 1
        done = done_events[0]
        assert "calculate_budget" in done["tools_used"]

        # Same guarantee as the non-tool streaming tests
        # (tests/test_chat_streaming.py): what's saved matches the done
        # event exactly, including the new tools_used field.
        conversation_id = done["conversation_id"]
        history = client.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()
        assert len(history["messages"]) == 1
        saved_reply = history["messages"][0]["assistant_reply"]
        assert saved_reply["summary"] == done["summary"]
        assert saved_reply["tools_used"] == done["tools_used"]

    def test_account_scoping_is_unchanged_for_a_message_that_used_a_tool(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        _signup(client_a, "ToolOwner")
        _signup(client_b, "ToolOther")

        reply = client_a.post("/api/v1/chat/", json={"message": BUDGET_TOOL_MESSAGE})
        assert reply.status_code == 200, reply.text
        conversation_id = reply.json()["conversation_id"]

        owner_conversations = client_a.get("/api/v1/chat/conversations").json()["conversations"]
        assert any(c["conversation_id"] == conversation_id for c in owner_conversations)

        other_conversations = client_b.get("/api/v1/chat/conversations").json()["conversations"]
        assert all(c["conversation_id"] != conversation_id for c in other_conversations)
        assert client_b.get(f"/api/v1/chat/conversations/{conversation_id}/messages").status_code == 404
