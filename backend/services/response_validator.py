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
from typing import Any, Callable, Dict, List

from json_utils import CHAT_RESPONSE_DEFAULTS
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

_DAY_COUNT_RE = re.compile(r"(\d{1,2})\s*-?\s*day")
_BUDGET_RE = re.compile(r"(\d[\d,]{3,})\s*(?:lkr|rs\.?|rupees)?", re.IGNORECASE)


def validate_schema(data: dict) -> List[str]:
    """Type-check the top-level ChatResponse fields."""
    issues: List[str] = []

    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        issues.append("summary is missing or empty")

    if data.get("destination") is not None and not isinstance(data["destination"], str):
        issues.append("destination must be a string or null")

    if data.get("days") is not None and not isinstance(data["days"], int):
        issues.append("days must be an integer or null")

    if data.get("budget_lkr") is not None and not isinstance(data["budget_lkr"], int):
        issues.append("budget_lkr must be an integer or null")

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

    return issues


def _response_text_blob(data: dict) -> str:
    """All response text in one lowercased string, for keyword matching."""
    parts = [data.get("summary") or "", data.get("destination") or ""]
    for day in data.get("itinerary") or []:
        if isinstance(day, dict):
            parts.append(day.get("title") or "")
            parts.extend(str(item) for item in (day.get("items") or []) if isinstance(item, str))
    return " ".join(parts).lower()


def matches_request(user_message: str, data: dict) -> List[str]:
    """Rule-based check that the response actually reflects what was asked.

    Deliberately lightweight: regex/keyword matching against the user's raw
    message, not another LLM call — see the module docstring.
    """
    issues: List[str] = []
    lower_message = user_message.lower()
    response_blob = _response_text_blob(data)

    day_match = _DAY_COUNT_RE.search(lower_message)
    if day_match:
        requested_days = int(day_match.group(1))
        if data.get("days") != requested_days:
            issues.append(
                f"user asked for {requested_days} days but response has days={data.get('days')}"
            )

    budget_match = _BUDGET_RE.search(lower_message)
    if budget_match:
        requested_budget = int(budget_match.group(1).replace(",", ""))
        response_budget = data.get("budget_lkr")
        tolerance = max(1000, requested_budget * 0.15)
        if response_budget is not None and abs(response_budget - requested_budget) > tolerance:
            issues.append(
                f"user mentioned a budget around {requested_budget} LKR but response has "
                f"budget_lkr={response_budget}"
            )

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


def _fallback_response(data: dict) -> Dict[str, Any]:
    """Last resort after a retry still fails — never let broken or
    mismatched data reach the UI. Salvages whatever parts of `data` are
    already valid instead of discarding a mostly-good response outright.
    """
    safe = dict(CHAT_RESPONSE_DEFAULTS)

    if isinstance(data.get("summary"), str) and data["summary"].strip():
        safe["summary"] = data["summary"]
    if isinstance(data.get("destination"), str):
        safe["destination"] = data["destination"]
    if isinstance(data.get("days"), int):
        safe["days"] = data["days"]
    if isinstance(data.get("budget_lkr"), int):
        safe["budget_lkr"] = data["budget_lkr"]
    if not validate_itinerary(data):
        safe["itinerary"] = data.get("itinerary", [])

    if not safe["summary"]:
        safe["summary"] = "I had trouble putting together an accurate plan for that request."

    safe["follow_up_question"] = (
        "Could you confirm the destination, number of days, and budget so I can get this right?"
    )

    return safe


def repair_or_retry(
    user_message: str,
    data: dict,
    retry_fn: Callable[[List[str]], dict],
    max_retries: int = 1,
) -> dict:
    """Validate `data`; if it fails, ask `retry_fn` for a corrected version
    (passing the specific issues found) up to `max_retries` times. Falls
    back to a safe response rather than ever returning invalid data.
    """
    attempt = 0

    while True:
        issues = validate_schema(data)
        if not issues:
            issues = validate_itinerary(data)
        if not issues:
            issues = matches_request(user_message, data)

        if not issues:
            return data

        if attempt >= max_retries:
            return _fallback_response(data)

        attempt += 1
        data = retry_fn(issues)


def validate_ai_response(
    user_message: str,
    raw_data: dict,
    retry_fn: Callable[[List[str]], dict],
    max_retries: int = 1,
) -> dict:
    """Entry point: validate a Groq response and repair/retry/fall back as needed."""
    return repair_or_retry(user_message, raw_data, retry_fn, max_retries)
