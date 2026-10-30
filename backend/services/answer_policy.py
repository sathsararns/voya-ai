"""Keep factual answers out of itinerary fields and unsupported rules out of prose."""

import re

from json_utils import CHAT_RESPONSE_DEFAULTS
from services.context_router import is_information_only_request, is_preference_statement
from services.transport_policy import is_transport_duration_question
from services.budget import extract_budget_cap, requests_saved_budget, resolve_budget_cap, saved_budget_range
from services.trip_state import keeps_budget, last_plan, needs_previous_trip, scoped_trip_history
from services.trip_constraints import stated_days, stated_nights


def non_trip_answer(message: str) -> bool:
    return is_information_only_request(message) or is_preference_statement(message)


def _clarification(summary: str, question: str) -> dict:
    return {**CHAT_RESPONSE_DEFAULTS, "summary": summary, "follow_up_question": question,
            "kb_sources": [], "tools_used": [], "_skip_preference_learning": True}


def planning_preflight_reply(message: str, history=None, preferences=None):
    history = scoped_trip_history(message, history)
    previous = last_plan(history)
    days, nights = stated_days(message), stated_nights(message)
    if days is None and needs_previous_trip(message) and previous:
        days = previous.get("days")
    if days is not None and nights is not None and nights > max(days - 1, 0):
        return _clarification(
            f"You requested {days} days and {nights} overnight stays. These do not fit a standard {days}-day schedule.",
            "Should I extend the trip, reduce the overnight stays, or include separate arrival/departure nights?",
        )
    if needs_previous_trip(message):
        if previous is None:
            return _clarification(
                "I don't have the previous itinerary in this chat, so I cannot preserve its trip details.",
                "Which trip should I change? Open the chat containing that itinerary, or paste the plan and the details you want to keep.",
            )
        if keeps_budget(message) and resolve_budget_cap(message, history) is None:
            return _clarification("The previous itinerary has no confirmed total amount to keep.",
                                  "What total budget should I preserve for this revised trip?")
    if requests_saved_budget(message) and extract_budget_cap(message) is None:
        limits = saved_budget_range(preferences)
        if limits is None:
            return _clarification("I don't have a confirmed saved budget range for your account.",
                                  "What total budget or range should I use? You can also save it in Settings.")
        cap = resolve_budget_cap(message, history)
        if cap is not None and cap < limits[0]:
            return _clarification(
                f"Your current trip cap of LKR {cap:,} is below your saved budget range of LKR {limits[0]:,}–{limits[1]:,}.",
                "Should I keep the current cap or use a different budget for this trip?",
            )
    return None


def normalize_answer_shape(message: str, data: dict) -> dict:
    if not non_trip_answer(message):
        return data
    result = dict(data)
    # Preserve useful generated facts while removing invented day containers.
    sections = [data.get("summary") or ""]
    for entry in data.get("itinerary") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("title"):
            sections.append(entry["title"])
        sections.extend("- " + item for item in entry.get("items", []) if isinstance(item, str))
    result.update(summary="\n".join(sections).strip(), itinerary=[], destination=None,
                  days=None, budget_lkr=None, budget_breakdown=None, fee_notice=None,
                  budget_cap_lkr=None, budget_source=None, budget_note=None,
                  assumptions=[], planning_context=None)
    return result


def information_fallback(message: str, sources: list[dict], retrieval_status: str = ""):
    if not is_information_only_request(message):
        return None
    # The current KB has static guide material, not a maintained official
    # nationality-specific visa rules source. Do not turn old/general facts
    # (or pretrained knowledge) into current eligibility or validity advice.
    if re.search(r"\b(?:visa|visas|eta|entry requirements?)\b", message, re.I):
        result = dict(CHAT_RESPONSE_DEFAULTS)
        result.update(
            summary=("Visa and entry requirements depend on your passport nationality, "
                     "travel purpose and dates. I cannot verify the current rules from "
                     "the available sources. Confirm the requirements with the official "
                     "Sri Lankan immigration authority before applying or travelling."),
            follow_up_question="What is your passport nationality, travel purpose and intended arrival date?",
        )
        return result
    if is_transport_duration_question(message):
        result = dict(CHAT_RESPONSE_DEFAULTS)
        result.update(
            summary=("I cannot verify a route-specific journey duration from the available sources. "
                     "Please confirm the route, connections and travel time with the transport operator."),
            follow_up_question=None,
        )
        return result
    if retrieval_status in {"no_supporting_evidence", "unavailable"} and not sources:
        result = dict(CHAT_RESPONSE_DEFAULTS)
        result.update(
            summary=("The travel knowledge lookup is temporarily unavailable, so I cannot verify that information. Please try again shortly."
                     if retrieval_status == "unavailable" else
                     "I cannot verify that information from the available travel sources."),
            follow_up_question=None,
        )
        return result
    return None
