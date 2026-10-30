"""Resolve active-trip references without borrowing an unrelated itinerary."""

import json
import re

from services.context_router import DESTINATIONS, is_itinerary_edit, is_options_request
from services.preference_policy import planning_text

_PLACES = DESTINATIONS - {"sri lanka", "sri lankan"}


def place_terms(text: str) -> set[str]:
    return {place for place in _PLACES if re.search(r"\b" + re.escape(place) + r"\b", text, re.I)}


def parse_plan(turn):
    if turn.get("role") != "assistant":
        return None
    try:
        data = json.loads(turn.get("content", ""))
    except (ValueError, TypeError):
        return None
    if (isinstance(data, dict) and not data.get("error_code")
            and isinstance(data.get("itinerary"), list) and data["itinerary"]):
        return data
    return None


def last_plan(history):
    return next((plan for turn in reversed(history or []) if (plan := parse_plan(turn)) is not None), None)


def needs_previous_trip(message: str) -> bool:
    message = planning_text(message)
    if re.search(r"\b(?:same|previous|existing)\s+(?:starting point|travell?ers?|number of travell?ers?|arrival date|travel dates|trip|itinerary)\b", message, re.I):
        return True
    return bool(is_itinerary_edit(message) and re.search(
        r"\b(?:it|this|that|same|previous|existing|current)\b|\b(?:day|night)\s+\d+\b", message, re.I,
    ))


def keeps_budget(message: str) -> bool:
    return bool(re.search(
        r"\b(?:keep|retain|maintain|same|unchanged)\b[^.!?\n]{0,180}\b(?:budget|spending cap|spending limit)\b"
        r"|\b(?:budget|spending cap|spending limit)\b[^.!?\n]{0,45}\b(?:same|unchanged)\b", message, re.I,
    ))


def destination_request(message: str) -> set[str]:
    if is_options_request(message):
        return set()
    match = re.search(
        r"\b(?:trip|tour|itinerary|holiday|vacation)\s+(?:to|in|around|through|for)\s+(.+?)"
        r"(?=\s+(?:with|using|starting|departing|for|budget|instead of)\b|[.!?;\n]|$)", message, re.I,
    )
    return place_terms(match[1]) if match else set()


def starts_new_trip(message: str, previous) -> bool:
    destinations = destination_request(message)
    if not destinations or needs_previous_trip(message):
        return False
    if re.search(r"\b(?:new|another|different)\s+(?:trip|tour|itinerary)\b|\bstart (?:over|fresh)\b", message, re.I):
        return True
    return previous is not None and destinations.isdisjoint(place_terms(str(previous.get("destination") or "")))


def scoped_trip_history(message: str, history=None) -> list[dict]:
    active = []
    previous = None
    for turn in history or []:
        if turn.get("role") == "user" and previous is None and needs_previous_trip(turn.get("content", "")):
            # An edit that could not be applied is not a source of facts for
            # a later self-contained plan (e.g. its orphaned "2 nights").
            continue
        if turn.get("role") == "user" and starts_new_trip(turn.get("content", ""), previous):
            active = []
            previous = None
        active.append(turn)
        plan = parse_plan(turn)
        if plan is not None:
            previous = plan
    return [] if starts_new_trip(message, previous) else active


def validate_followup_identity(message: str, data: dict, history=None) -> list[str]:
    if not data.get("itinerary") or not needs_previous_trip(message):
        return []
    previous = last_plan(scoped_trip_history(message, history))
    if previous is None:
        return ["This edit refers to a previous itinerary that is unavailable. Ask for that plan; do not invent another destination."]
    old_places = place_terms(str(previous.get("destination") or ""))
    new_places = place_terms(message)
    if new_places - old_places and not re.search(r"\b(?:same|keep|retain)\b.{0,35}\bdestinations?\b", message, re.I):
        return []  # an explicit destination change supersedes the old destination
    actual = str(data.get("destination") or "")
    missing = old_places - place_terms(actual)
    if missing:
        return [f"This is an edit to the previous {previous.get('destination')} itinerary. Preserve its destination(s): {', '.join(sorted(missing))}. Do not replace it with a different trip."]
    if not old_places and actual.strip().lower() != str(previous.get("destination") or "").strip().lower():
        return ["Keep the previous itinerary's destination, or ask which destination to change it to."]
    return []
