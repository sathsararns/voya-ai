"""Measure request count and context retention without paid/provider calls."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services import groq_service
from services.context_router import decide_context_strategy
from services.local_context import local_tool_context
from services.prompt_context import compact_history


REQUEST = (
    "Plan a 3-day solo trip to Kandy starting from Colombo, with 2 overnight stays. "
    "My total budget is LKR 45000, including accommodation, vegetarian meals, "
    "transport and activities. Prefer train travel. Show the budget breakdown "
    "and disclose assumptions. Do not invent entrance fees."
)
PLAN = {
    "destination": "Kandy", "days": 3, "budget_lkr": 45000,
    "summary": "A provisional Kandy trip with vegetarian meals and train travel. Costs need confirmation.",
    "itinerary": [
        {"day": 1, "title": "Colombo to Kandy", "items": ["Travel by train; confirm connections.", "Walk by Kandy Lake."]},
        {"day": 2, "title": "Kandy culture", "items": ["Visit the Temple of the Tooth; entry fee unverified.", "Vegetarian lunch."]},
        {"day": 3, "title": "Return to Colombo", "items": ["Visit Kandy market.", "Return by train; confirm connections."]},
    ],
    "follow_up_question": None,
}
HIT = {"id": "kandy-guide", "score": 0.8, "fields": {
    "text": "Kandy Lake and the Temple of the Sacred Tooth are Kandy attractions.",
    "document_name": "Guide-Book-Sri-Lanka.pdf", "title": "Sri Lanka Travel Guide", "page_number": 2,
}}


@pytest.mark.parametrize("streaming", [False, True])
def test_normal_plan_uses_one_completion_and_one_kb_search(monkeypatch, streaming):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    kb = MagicMock(return_value=[HIT])
    monkeypatch.setattr(groq_service, "search_knowledge_base", kb)
    monkeypatch.setattr(groq_service, "format_memory_context", lambda *_a, **_k: "")
    client = MagicMock()
    choice = SimpleNamespace(message=SimpleNamespace(content=json.dumps(PLAN)), finish_reason="stop")
    stream = MagicMock()
    stream.__iter__.return_value = iter([SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=json.dumps(PLAN)), finish_reason="stop")])])
    client.chat.completions.create.return_value = stream if streaming else SimpleNamespace(choices=[choice])
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(groq_service, "Groq", factory)
    candidates = decide_context_strategy(REQUEST, 0).candidate_tools
    kwargs = dict(history=[], use_memory=False, candidate_tools=candidates)
    if streaming:
        result = next(payload for event, payload in groq_service.stream_groq_reply(REQUEST, **kwargs) if event == "done")
        stream.close.assert_called_once()
    else:
        result = groq_service.get_groq_reply(REQUEST, **kwargs)
    assert not result.get("error_code"), result
    assert result["days"] == 3 and result["budget_lkr"] == 45000
    assert result["budget_breakdown"]["total_lkr"] == 45000
    assert result["planning_context"]["nights"] == 2
    assert result["tools_used"] == ["calculate_budget"]
    assert result["kb_sources"][0]["document_name"] == HIT["fields"]["document_name"]
    factory.assert_called_once()
    client.chat.completions.create.assert_called_once()
    kb.assert_called_once()
    sent = client.chat.completions.create.call_args.kwargs["messages"]
    assert sum(m["content"].count("The server computes the single authoritative") for m in sent) == 1
    assert "Brief Garden" not in sent[0]["content"]
    assert "entry fee" in sent[0]["content"]


def test_compaction_keeps_options_latest_plan_and_all_user_constraints():
    options = {"summary": "1) Kandy 2 days. 2) Galle 5 days.", "itinerary": []}
    older = {**copy.deepcopy(PLAN), "kb_sources": [{"snippet": "s" * 3000}],
             "budget_breakdown": {"table": "b" * 2000}}
    current = {**copy.deepcopy(PLAN), "planning_context": {"nights": 2, "nights_source": "user"},
               "budget_cap_lkr": 45000, "kb_sources": [{"snippet": "c" * 3000}]}
    history = [
        {"role": "user", "content": "give options"},
        {"role": "assistant", "content": json.dumps(options)},
        {"role": "user", "content": REQUEST},
        {"role": "assistant", "content": json.dumps(older)},
        {"role": "user", "content": "Keep 3 days, 2 nights and both requested venues. Do not save preferences."},
        {"role": "assistant", "content": json.dumps(current)},
    ]
    original = copy.deepcopy(history)
    compact = compact_history(history)
    assert history == original
    assert [t for t in compact if t["role"] == "user"] == [t for t in history if t["role"] == "user"]
    assert json.loads(compact[1]["content"]) == options
    latest = json.loads(compact[-1]["content"])
    assert latest["itinerary"] == PLAN["itinerary"]
    assert latest["planning_context"] == current["planning_context"]
    assert latest["budget_cap_lkr"] == 45000
    assert "kb_sources" not in latest
    assert len(json.dumps(compact)) < len(json.dumps(history)) / 2


@pytest.mark.parametrize("content", ["plain answer", "[]", "null"])
def test_unstructured_history_is_preserved(content):
    history = [{"role": "assistant", "content": content}]
    assert compact_history(history) == history


def test_weather_lookup_is_local_and_not_a_live_forecast():
    context, used = local_tool_context("What is the climate in Kandy?", ["get_weather"])
    assert used == ["get_weather"]
    assert '"is_live_forecast":false' in context
    assert "hill country" in context


@pytest.mark.parametrize("message", ["Weather for option 2?", "What is the weather?", "How much is the trip?"])
def test_missing_tool_arguments_are_not_invented(message):
    assert local_tool_context(message, ["get_weather", "calculate_budget"]) == ("", [])


def test_options_do_not_invent_a_combined_budget():
    assert local_tool_context("Give travel options: Kandy 2 days or Galle 5 days, LKR 45000.",
                              ["calculate_budget", "generate_itinerary"]) == ("", [])


def test_budget_followup_reuses_days_and_cap_without_an_ai_call():
    history = [{"role": "user", "content": REQUEST}, {"role": "assistant", "content": json.dumps(PLAN)}]
    context, used = local_tool_context("Reduce the total budget to LKR 30000.", ["calculate_budget"], history)
    assert used == ["calculate_budget"]
    assert '"days":3' in context and '"total_budget_lkr":30000' in context


def test_places_and_itinerary_candidates_do_not_repeat_retrieval():
    assert local_tool_context(REQUEST, ["search_places", "generate_itinerary"]) == ("", [])
