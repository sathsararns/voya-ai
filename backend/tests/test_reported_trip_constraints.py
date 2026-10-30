"""Reported overnight, trip-only budget and unverified-feasibility regressions."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from memory_utils import build_memory_text
from routes import chat as routes
from schemas.chat import ChatRequest
from services import groq_service
from services.answer_policy import planning_preflight_reply
from services.budget import requests_saved_budget, validate_budget_feasibility
from services.context_router import is_itinerary_edit
from services.groq_service import _prepare_reply
from services.planning_context import attach_planning_context, planning_context, validate_planning_context
from services.response_validator import matches_request
from services.trip_state import needs_previous_trip, scoped_trip_history
from services.user_preferences import extract_and_merge_preferences
from services.trip_constraints import stated_days, stated_nights


FIRST = 'Plan a 3-day solo trip to Kandy starting from Colombo, with 2 overnight stays. My total budget is LKR 45000, including accommodation, vegetarian meals, transport and activities. Prefer train travel. Show the budget breakdown, disclose assumptions and do not invent entrance fees.'
TEMPORARY = 'For this trip only, my total budget is LKR 18000. This is not my usual budget. Do not update my saved budget preference. Plan a one-day trip to Galle with no overnight stay.'
SAVED = {'budget_lkr_min': 30000, 'budget_lkr_max': 45000, 'budget_preference_source': 'user_provided', 'food_preferences': ['Vegetarian']}


def plan(destination='Kandy', days=3, total=45000):
    return {'destination': destination, 'days': days, 'budget_lkr': total,
            'summary': f'A {days}-day {destination} itinerary with vegetarian meals and train travel.',
            'itinerary': [{'day': n, 'title': destination, 'items': ['Explore the local sights', 'Vegetarian lunch']}
                          for n in range(1, days + 1)], 'follow_up_question': None}


@pytest.mark.parametrize('phrase,count', [
    ('2 overnight stays', 2), ('two overnights', 2), ('1 overnight stay', 1),
    ('one overnight stay', 1), ('a 1-night stay', 1), ('two-night stay', 2),
    ('no overnight stay', 0), ('without any overnight stays', 0), ('0 nights', 0),
])
def test_explicit_overnights_are_user_facts_not_assumptions(phrase, count):
    context = planning_context(f'Plan a 3-day trip to Kandy with {phrase}.')
    assert context['nights'] == count and context['nights_source'] == 'user'


def test_reported_initial_and_followup_nights_are_not_labelled_assumed():
    first = attach_planning_context(plan(), FIRST)
    assert first['planning_context']['nights_source'] == 'user'
    assert all('Overnight stays' not in assumption for assumption in first['assumptions'])
    followup = 'Make it 2 days with 1 overnight stay. Keep the same destination, starting point, 4 adults, vegetarian meals, train preference and LKR 45000 total budget.'
    updated = attach_planning_context(plan(days=2), followup, [{'role': 'user', 'content': FIRST}, {'role': 'assistant', 'content': json.dumps(first)}])
    assert updated['planning_context']['nights'] == 1
    assert updated['planning_context']['nights_source'] == 'user'
    assert updated['planning_context']['travellers'] == 4
    assert all('Overnight stays' not in assumption for assumption in updated['assumptions'])


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('duration', ['2-day', 'two-day', '2‑day'])
def test_conflicting_nights_are_clarified_before_any_provider_call(monkeypatch, streaming, duration):
    model = MagicMock(side_effect=AssertionError('No model quota should be spent'))
    search = MagicMock(side_effect=AssertionError('No KB lookup needed'))
    monkeypatch.setattr(groq_service, 'Groq', model)
    monkeypatch.setattr(groq_service, 'search_knowledge_base', search)
    message = f'Plan a {duration} trip to Kandy with 3 overnight stays. Both requirements are mandatory. If they conflict, ask me to clarify.'
    if streaming:
        reply = next(data for event, data in groq_service.stream_groq_reply(message) if event == 'done')
    else:
        reply = groq_service.get_groq_reply(message)
    assert reply['itinerary'] == [] and reply['kb_sources'] == []
    assert reply['follow_up_question'] and '3 overnight stays' in reply['summary']
    model.assert_not_called()
    search.assert_not_called()


def test_standalone_galle_plan_with_optout_does_not_require_an_old_itinerary():
    assert not needs_previous_trip(TEMPORARY)
    assert not is_itinerary_edit(TEMPORARY)
    assert not requests_saved_budget(TEMPORARY)
    assert planning_preflight_reply(TEMPORARY, [], SAVED) is None
    old = [{'role': 'user', 'content': FIRST}, {'role': 'assistant', 'content': json.dumps(plan())}]
    assert scoped_trip_history(TEMPORARY, old) == []


def test_one_day_temporary_plan_has_zero_accommodation_and_no_persistent_learning():
    result = _prepare_reply(TEMPORARY, plan('Galle', 1, 18000), MagicMock(side_effect=AssertionError('Valid plan needs no retry')), user_preferences=SAVED)
    assert result['days'] == 1 and result['budget_lkr'] == 18000
    assert result['planning_context']['nights'] == 0
    assert result['planning_context']['nights_source'] == 'user'
    assert result['budget_breakdown']['accommodation_lkr'] == 0
    assert result['budget_breakdown']['total_lkr'] == 18000
    assert result['budget_source'] == 'user_provided'
    original = copy.deepcopy(SAVED)
    assert extract_and_merge_preferences(SAVED, TEMPORARY, result) == original
    assert SAVED == original
    assert build_memory_text(TEMPORARY, result) == ''


def test_trip_only_policy_survives_followups_but_not_an_unrelated_new_trip():
    first = _prepare_reply(TEMPORARY, plan('Galle', 1, 18000), MagicMock(), user_preferences=SAVED)
    history = [{'role': 'user', 'content': TEMPORARY}, {'role': 'assistant', 'content': json.dumps(first)}]
    followup = _prepare_reply('Make the itinerary more relaxed.', plan('Galle', 1, 18000), MagicMock(), history=history, user_preferences=SAVED)
    assert extract_and_merge_preferences(SAVED, 'Change the total budget to LKR 15000', followup) == SAVED
    assert build_memory_text('Change the total budget to LKR 15000', followup) == ''
    fresh = _prepare_reply(FIRST, plan(), MagicMock(), history=history, user_preferences=SAVED)
    assert not fresh.get('_skip_preference_learning')
    assert extract_and_merge_preferences(SAVED, FIRST, fresh)['budget_lkr_max'] == 45000


def test_ordinary_budget_and_explicit_saved_preference_request_still_work():
    assert needs_previous_trip('Make it 3 days instead. Do not change my budget.')
    assert requests_saved_budget('Use my saved budget preference.')
    updated = extract_and_merge_preferences({}, 'My total budget is LKR 45000', plan())
    assert updated['budget_lkr_min'] == 45000
    history = [{'role': 'assistant', 'content': json.dumps({**plan(), '_skip_preference_learning': True})}]
    requested = 'Remember this as my usual budget preference. My total budget is LKR 45000.'
    result = _prepare_reply(requested, plan(), MagicMock(), history=history)
    assert not result.get('_skip_preference_learning')


@pytest.mark.parametrize('summary', [
    'This trip is not feasible for four adults.',
    'The budget is insufficient for even basic accommodation and transport costs.',
    'This plan is definitely affordable.',
    'This is guaranteed within your budget.',
])
def test_unverified_categorical_budget_judgements_are_rejected(summary):
    assert validate_budget_feasibility({'summary': summary, 'itinerary': []}, 45000)


@pytest.mark.parametrize('summary', [
    'I cannot confirm whether the budget is sufficient without current quotes.',
    'A tentative plan using planning allowances, not verified prices.',
    'You may need a higher budget; please confirm current accommodation rates.',
    "I couldn't produce a reliable plan within your total budget.",
])
def test_honest_feasibility_uncertainty_is_allowed(summary):
    assert validate_budget_feasibility({'summary': summary}) == []


def test_budget_check_does_not_reject_unrelated_logistical_clarifications():
    assert validate_budget_feasibility({'summary': 'That schedule is not possible without changing the number of nights.'}) == []
    assert validate_budget_feasibility({'summary': 'The budget is insufficient, although costs may vary.'}, 45000)


def test_corrected_day_and_night_counts_do_not_use_the_rejected_old_values():
    assert stated_days('Make it 3 days instead of 2 days.') == 3
    assert stated_nights('Use 2 overnight stays, not 1 night.') == 2


def test_reported_four_person_refusal_is_retried_instead_of_claiming_impossibility():
    message = 'Now change this to 4 adults. LKR 45000 is the total budget for all four people combined, not per person. Keep the same destinations, starting point, 3 days and 2 nights. Explain the compromises and disclose unverified costs. If you cannot support a feasible plan, say so.'
    refusal = {**plan(), 'itinerary': [], 'summary': 'Covering accommodation, vegetarian meals, train travel and activities over 3 days and 2 nights is not feasible. The budget is insufficient for basic costs.'}
    corrected = {**refusal, 'summary': 'I cannot confirm whether the total budget covers current accommodation and transport costs for four adults. Vegetarian meals and train travel remain preferences. Budget rooms, public transport and fewer paid activities may help; obtain quotes before confirming.', 'follow_up_question': 'What are your travel dates and accommodation quotes?'}
    retry = MagicMock(return_value=corrected)
    result = _prepare_reply(message, refusal, retry, history=[{'role': 'user', 'content': FIRST}, {'role': 'assistant', 'content': json.dumps(plan())}])
    retry.assert_called_once()
    assert not result.get('error_code') and 'cannot confirm' in result['summary']
    assert 'not feasible' not in result['summary']


def test_spelled_one_day_count_and_overnight_summary_mismatches_are_checked():
    assert matches_request('Plan a one-day trip to Galle.', plan('Galle', 3))
    assert validate_planning_context(FIRST, {**plan(), 'summary': 'A Kandy plan with three overnight stays.'})


@pytest.mark.parametrize('streaming', [False, True])
def test_both_chat_routes_save_temporary_chat_without_updating_profile_or_vector_memory(monkeypatch, streaming):
    reply = _prepare_reply(TEMPORARY, plan('Galle', 1, 18000), MagicMock(), user_preferences=SAVED)
    monkeypatch.setattr(routes, 'get_or_create_conversation', lambda *_a, **_k: SimpleNamespace(conversation_id='temporary'))
    monkeypatch.setattr(routes, 'count_conversation_messages', lambda *_: 0)
    save, update, memory = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(routes, 'save_chat', save)
    monkeypatch.setattr(routes, 'touch_conversation', MagicMock())
    monkeypatch.setattr(routes, 'update_user_preferences', update)
    monkeypatch.setattr(routes, 'save_memory', memory)
    monkeypatch.setattr(routes, 'get_groq_reply', lambda *_a, **_k: reply)
    def stream(*_a, **_k):
        yield 'done', reply
    monkeypatch.setattr(routes, 'stream_groq_reply', stream)
    request = ChatRequest(message=TEMPORARY, session_id='temporary')
    user = SimpleNamespace(id='temporary-user', preferences=copy.deepcopy(SAVED))
    if streaming:
        class Connected:
            async def is_disconnected(self):
                return False
        response = routes.chat_stream(request, Connected(), db=object(), current_user=user)
        async def consume():
            return ''.join([chunk async for chunk in response.body_iterator])
        assert '18000' in asyncio.run(consume())
    else:
        assert routes.chat(request, db=object(), current_user=user)['budget_lkr'] == 18000
    save.assert_called_once()
    update.assert_not_called()
    memory.assert_not_called()
