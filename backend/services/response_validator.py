"""Self-RAG style validation for Groq responses.

Runs three layers of checks on the model's structured output before it's
allowed to reach save/return:
  1. Schema validation    — are the top-level fields the right types?
  2. Itinerary validation — does each day have day/title/items, items a
                             list of strings?
  3. Request-match validation — does the response actually reflect what the
                             user asked for (day count, budget, destination,
                             stated preferences)?

If any layer fails, the caller gets one chance to retry with a stricter,
issue-specific prompt (via retry_fn — see groq_service.py). If it still
fails after that, a safe, schema-valid fallback is returned instead of ever
handing broken or mismatched data back to routes/chat.py — the UI never
sees invalid output.

This is deliberately rule-based, not another LLM call for judging the
response — same lightweight philosophy as context_router.py.
"""

import re
import logging
from typing import Any, Callable, Dict, List

from json_utils import CHAT_RESPONSE_DEFAULTS
from services.context_router import is_options_request
from services.answer_policy import non_trip_answer
from services.budget import resolve_budget_cap, validate_budget_presentation, validate_budget_origin, validate_saved_budget_request, validate_budget_feasibility
from services.trip_constraints import stated_days
from services.destination_spelling import validate_destination_spelling
from services.planning_context import validate_planning_context
from services.transport_policy import validate_transport_claims
from services.trip_state import scoped_trip_history, validate_followup_identity

logger = logging.getLogger(__name__)
from services.fee_policy import validate_fee_claims
from services.venue_facts import validate_required_venues, validate_venue_placement
from services.itinerary_edits import validate_attraction_addition
from services.history_manager import HOTEL_KEYWORDS, TRANSPORT_KEYWORDS, FOOD_KEYWORDS

# A small, curated set of Sri Lankan destinations — enough to catch the
# common "user said X but the plan is about Y" mismatch without needing a
# full gazetteer or an LLM call.
KNOWN_DESTINATIONS = [
    "kandy", "colombo", "galle", "ella", "nuwara eliya", "sigiriya", "jaffna",
    "trincomalee", "mirissa", "bentota", "anuradhapura", "polonnaruwa",
    "dambulla", "yala", "udawalawe", "negombo", "hikkaduwa", "arugam bay",
    "kalpitiya", "kitulgala", "matara", "batticaloa",
]

def validate_schema(data: dict) -> List[str]:
    """Type-check the top-level ChatResponse fields."""
    issues: List[str] = []

    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        issues.append("summary is missing or empty")

    if data.get("destination") is not None and not isinstance(data["destination"], str):
        issues.append("destination must be a string or null")

    if data.get("days") is not None and not isinstance(data["days"], int):
        issues.append("days must be an integer or null")

    if data.get("budget_lkr") is not None and (
        type(data["budget_lkr"]) is not int or data["budget_lkr"] < 0
    ):
        issues.append("budget_lkr must be a non-negative integer or null")

    if not isinstance(data.get("itinerary"), list):
        issues.append("itinerary must be a list")

    if data.get("follow_up_question") is not None and not isinstance(
        data["follow_up_question"], str
    ):
        issues.append("follow_up_question must be a string or null")

    return issues


def validate_itinerary(data: dict) -> List[str]:
    """Check each itinerary entry has day/title/items in the right shape."""
    itinerary = data.get("itinerary")
    if not isinstance(itinerary, list):
        return ["itinerary must be a list"]

    issues: List[str] = []
    for i, entry in enumerate(itinerary):
        if not isinstance(entry, dict):
            issues.append(f"itinerary[{i}] must be an object")
            continue
        if not isinstance(entry.get("day"), int):
            issues.append(f"itinerary[{i}].day must be an integer")
        if not isinstance(entry.get("title"), str) or not entry["title"].strip():
            issues.append(f"itinerary[{i}].title must be a non-empty string")
        items = entry.get("items")
        if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
            issues.append(f"itinerary[{i}].items must be a list of strings")
        else:
            for item in items:
                # Escaped JSON fragments can be valid strings yet still
                # leak array delimiters or an unmatched quote into the UI.
                if re.search(r'"\s*,\s*"', item) or (
                    item.rstrip().endswith('"') and item.count('"') % 2
                ):
                    issues.append(
                        f"itinerary[{i}].items contains JSON punctuation; "
                        "return each activity as a separate clean string"
                    )
                    break

    return issues


def _response_text_blob(data: dict) -> str:
    """All response text in one lowercased string, for keyword matching."""
    parts = [data.get("summary") or "", data.get("destination") or ""]
    for day in data.get("itinerary") or []:
        if isinstance(day, dict):
            parts.append(day.get("title") or "")
            parts.extend(str(item) for item in (day.get("items") or []) if isinstance(item, str))
    return " ".join(parts).lower()


def matches_request(user_message: str, data: dict, budget_cap_lkr=None) -> List[str]:
    """Rule-based check that the response actually reflects what was asked.

    Deliberately lightweight: regex/keyword matching against the user's raw
    message, not another LLM call — see the module docstring.
    """
    issues: List[str] = []
    lower_message = user_message.lower()
    response_blob = _response_text_blob(data)

    if non_trip_answer(user_message):
        if data.get("itinerary"):
            issues.append("Answer this factual question or preference acknowledgement in summary, with itinerary empty.")
        return issues

    if is_options_request(user_message):
        if data.get("itinerary"):
            issues.append(
                "user requested travel options, not a detailed itinerary; "
                "describe the options in summary, leave itinerary empty, "
                "and ask the user to choose an option"
            )
        # Different alternatives can have different durations, destinations
        # and budgets. Do not apply one trip's scalar constraints to them.
        return issues

    requested_days = stated_days(user_message)
    if requested_days is not None and (data.get("itinerary") or data.get("days") is not None):
        if data.get("days") != requested_days:
            issues.append(
                f"user asked for {requested_days} days but response has days={data.get('days')}"
            )

    cap = budget_cap_lkr if budget_cap_lkr is not None else resolve_budget_cap(user_message)
    if cap is not None:
        response_budget = data.get("budget_lkr")
        if response_budget is not None and response_budget > cap:
            issues.append(
                f"response budget_lkr={response_budget} exceeds the user's total "
                f"budget cap of {cap} LKR; return a plan at or below the cap"
            )
        elif response_budget is None and data.get("itinerary"):
            issues.append(f"include a total budget_lkr at or below the user's {cap} LKR cap")

    for place in KNOWN_DESTINATIONS:
        if place in lower_message and place not in response_blob:
            issues.append(f"user mentioned '{place}' but it doesn't appear in the response")
            break

    for keyword_map, label in (
        (HOTEL_KEYWORDS, "accommodation"),
        (TRANSPORT_KEYWORDS, "transport"),
        (FOOD_KEYWORDS, "food"),
    ):
        for keyword in keyword_map:
            if keyword in lower_message and keyword not in response_blob:
                issues.append(
                    f"user mentioned {label} preference '{keyword}' but it isn't reflected in the plan"
                )
                break

    return issues


def _fallback_response(data: dict, budget_cap_lkr=None, issues=()) -> Dict[str, Any]:
    """Do not return prose or activities that failed semantic validation."""
    safe = dict(CHAT_RESPONSE_DEFAULTS)
    safe["error_code"] = "validation_failed"
    if budget_cap_lkr is not None:
        budget_failed = any("budget_lkr" in issue or "budget cap" in issue for issue in issues)
        safe["summary"] = (
            f"I couldn't produce a reliable plan within your LKR {budget_cap_lkr:,} total budget."
            if budget_failed else "I couldn't validate a consistent itinerary for that request."
        )
        safe["budget_cap_lkr"] = budget_cap_lkr
    else:
        safe["summary"] = "I couldn't produce a reliable plan for that request."
    safe["follow_up_question"] = None
    return safe


def repair_or_retry(
    user_message: str,
    data: dict,
    retry_fn: Callable[[List[str]], dict],
    max_retries: int = 1,
    history=None,
    user_preferences=None,
) -> dict:
    """Validate `data`; if it fails, ask `retry_fn` for a corrected version
    (passing the specific issues found) up to `max_retries` times. Falls
    back to a safe response rather than ever returning invalid data.
    """
    attempt = 0
    history = scoped_trip_history(user_message, history)
    budget_cap_lkr = resolve_budget_cap(user_message, history)

    while True:
        issues = validate_schema(data)
        failed_checks = ["schema"] if issues else []
        if not issues:
            issues = validate_itinerary(data)
            failed_checks = ["itinerary"] if issues else []
        if not issues:
            # Return all semantic problems in one correction round. Fixing
            # only the first issue can leave another one after the sole retry.
            checks = [
                ("request", lambda: matches_request(user_message, data, budget_cap_lkr)),
                ("fees", lambda: validate_fee_claims(data)),
                ("budget_presentation", lambda: validate_budget_presentation(data, history)),
                ("venue_placement", lambda: validate_venue_placement(data, KNOWN_DESTINATIONS)),
                ("required_venues", lambda: validate_required_venues(user_message, data, history)),
                ("attraction_addition", lambda: validate_attraction_addition(user_message, data, history)),
                ("destination_spelling", lambda: validate_destination_spelling(user_message, data, KNOWN_DESTINATIONS, history)),
                ("budget_origin", lambda: validate_budget_origin(data, budget_cap_lkr, user_preferences)),
                ("budget_feasibility", lambda: validate_budget_feasibility(data, budget_cap_lkr)),
                ("planning_context", lambda: validate_planning_context(user_message, data, history)),
                ("transport", lambda: validate_transport_claims(data)),
                ("followup_identity", lambda: validate_followup_identity(user_message, data, history)),
                ("saved_budget_request", lambda: validate_saved_budget_request(user_message, data, user_preferences, history)),
            ]
            for name, check in checks:
                found = check()
                if found:
                    failed_checks.append(name)
                    issues.extend(found)

        if not issues:
            return data

        if attempt >= max_retries:
            logger.warning("response_validation_failed checks=%s retries=%s", ",".join(failed_checks), attempt)
            return _fallback_response(data, budget_cap_lkr, issues)

        attempt += 1
        data = retry_fn(issues)


def validate_ai_response(
    user_message: str,
    raw_data: dict,
    retry_fn: Callable[[List[str]], dict],
    max_retries: int = 1,
    history=None,
    user_preferences=None,
) -> dict:
    """Entry point: validate a Groq response and repair/retry/fall back as needed."""
    return repair_or_retry(user_message, raw_data, retry_fn, max_retries, history=history, user_preferences=user_preferences)
