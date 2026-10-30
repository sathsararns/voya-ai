"""Keep factual answers out of itinerary fields and unsupported rules out of prose."""

import re

from json_utils import CHAT_RESPONSE_DEFAULTS
from services.context_router import is_information_only_request, is_preference_statement


def non_trip_answer(message: str) -> bool:
    return is_information_only_request(message) or is_preference_statement(message)


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
