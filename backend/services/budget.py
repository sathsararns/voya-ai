"""Budget caps and planning allocations; no live pricing is implied."""

import json
import re
from decimal import Decimal
from typing import Optional

from services.context_router import is_options_request

DAILY_BUDGET_LKR_BY_STYLE = {"budget": 8_000, "mid-range": 18_000, "luxury": 45_000}
BREAKDOWN_PERCENTAGES_BY_STYLE = {
    "budget": {"accommodation": 30, "food": 30, "transport": 25, "activities": 10, "misc": 5},
    "mid-range": {"accommodation": 35, "food": 25, "transport": 20, "activities": 15, "misc": 5},
    "luxury": {"accommodation": 45, "food": 20, "transport": 15, "activities": 15, "misc": 5},
}

BUDGET_PRESENTATION_INSTRUCTION = (
    "The server computes the single authoritative estimated budget breakdown "
    "from budget_lkr. Do not invent or repeat separate meal, hotel, transport, "
    "activity or per-day prices in itinerary titles/items or summary. Keep "
    "itinerary activities descriptive; the UI shows category allowances "
    "separately. The summary may mention the current trip total and an "
    "explicitly stated previous total when explaining a budget reduction. "
    "Do not claim breakfast, rooms or transport are included in a booking: "
    "there are no verified booking quotes.\n"
)

_AMOUNT = r"(?P<amount>\d[\d,]*(?:\.\d+)?)(?:\s*(?P<scale>k|thousand))?\b"
_CURRENCY = r"(?:LKR|Rs\.?|rupees)"
_DISPLAY_AMOUNTS = [
    re.compile(r"\b(?:LKR|Rs\.?)\s*" + _AMOUNT, re.IGNORECASE),
    re.compile(_AMOUNT + r"\s*(?:LKR|rupees)\b", re.IGNORECASE),
]
_BUDGET_PATTERNS = [
    re.compile(r"\bbudget\s*(?:(?:is|of|to|around|under|at|maximum|about)\s+)?[:=]?\s*"
               + _CURRENCY + r"?\s*" + _AMOUNT, re.IGNORECASE),
    re.compile(r"\b" + _CURRENCY + r"\s*" + _AMOUNT, re.IGNORECASE),
    re.compile(_AMOUNT + r"\s*" + _CURRENCY + r"\b", re.IGNORECASE),
    re.compile(r"\bbudget\s*(?:(?:is|of|to|around|under|at|maximum|about)\s+)?[:=]?\s*"
               + _AMOUNT, re.IGNORECASE),
]


def extract_budget_cap(message: str) -> Optional[int]:
    if is_options_request(message):
        return None
    if re.search(r"\b(?:USD|EUR|GBP)\b|[$€£]", message, re.IGNORECASE) and not re.search(
        _CURRENCY, message, re.IGNORECASE
    ):
        return None
    budget_cue = re.search(r"\b(?:budget|spend|afford|maximum|cap|limit|under|within|total)\b",
                           message, re.IGNORECASE)
    planning_cue = re.search(r"\b(?:trip|itinerary|plan)\b", message, re.IGNORECASE)
    amount_only = re.fullmatch(r"\s*" + _CURRENCY + r"\s*" + _AMOUNT + r"\s*[.!]?\s*",
                              message, re.IGNORECASE)
    if not (budget_cue or planning_cue or amount_only):
        return None
    if re.search(r"\b(?:entry|entrance|admission|ticket)\b", message, re.IGNORECASE) and not re.search(
        r"\bbudget\b", message, re.IGNORECASE
    ):
        return None
    # A per-day/per-night allowance must not be mistaken for the trip total.
    if re.search(r"\bper\s+(?:day|night|person)\b", message, re.IGNORECASE) and not re.search(
        r"\btotal\b", message, re.IGNORECASE
    ):
        return None
    for pattern in _BUDGET_PATTERNS:
        match = pattern.search(message)
        if match:
            amount = Decimal(match["amount"].replace(",", ""))
            if match["scale"]:
                amount *= 1000
            return int(amount)
    return None


def resolve_budget_cap(message: str, history=None) -> Optional[int]:
    if is_options_request(message):
        return None
    current = extract_budget_cap(message)
    if current is not None:
        return current
    for turn in reversed(history or []):
        if turn.get("role") == "user":
            previous = extract_budget_cap(turn.get("content", ""))
            if previous is not None:
                return previous
        elif turn.get("role") == "assistant":
            # Only server-owned cap metadata is trusted; a model-suggested
            # budget_lkr alone must not become a user-imposed spending cap.
            try:
                reply = json.loads(turn.get("content", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            cap = reply.get("budget_cap_lkr") if isinstance(reply, dict) else None
            if type(cap) is int and cap >= 0:
                return cap
    return None


def calculate_budget(days: int, total_budget_lkr: Optional[int] = None,
                     travel_style: Optional[str] = None) -> dict:
    days = max(1, min(int(days), 60))
    style = (travel_style or "mid-range").strip().lower()
    if style not in DAILY_BUDGET_LKR_BY_STYLE:
        style = "mid-range"
    total = int(total_budget_lkr) if total_budget_lkr is not None and total_budget_lkr > 0 else (
        DAILY_BUDGET_LKR_BY_STYLE[style] * days
    )
    breakdown = {
        f"{category}_lkr": total * percentage // 100
        for category, percentage in BREAKDOWN_PERCENTAGES_BY_STYLE[style].items()
    }
    # Keep the sum exact for amounts that do not divide evenly.
    breakdown["misc_lkr"] += total - sum(breakdown.values())
    return {
        "days": days, "travel_style": style, "total_budget_lkr": total,
        "daily_budget_lkr": round(total / days), "breakdown_lkr": breakdown,
        "is_estimate": True,
    }


def _display_amounts(text: str) -> list[Decimal]:
    amounts = []
    for pattern in _DISPLAY_AMOUNTS:
        for match in pattern.finditer(text):
            value = Decimal(match["amount"].replace(",", ""))
            amounts.append(value * 1000 if match["scale"] else value)
    return amounts


def validate_budget_presentation(data: dict, history=None) -> list[str]:
    """Prevent a second, unaccounted price list alongside the server ledger.

    The product has category allowances, not verified line-item quotes.
    Invented prices must be regenerated, not silently stripped or relabeled.
    """
    if not data.get("itinerary"):
        return []
    issues = []
    for day in data["itinerary"]:
        for text in [day["title"], *day["items"]]:
            if _display_amounts(text):
                issues.append(
                    f"Day {day['day']} contains a separate unaccounted price: {text!r}. "
                    "Remove inline currency amounts; describe the activity and "
                    "let the server's budget breakdown show the allowances."
                )
            if re.search(r"\bincluded\b", text, re.IGNORECASE) and re.search(
                r"\b(?:breakfast|lunch|dinner|meals?|rooms?|hotel|hostel|guesthouse|transport)\b",
                text, re.IGNORECASE,
            ):
                issues.append(
                    f"Day {day['day']} assumes an unverified booking inclusion: {text!r}. "
                    "Do not promise included meals, rooms or transport."
                )
    # A summary is not a second category ledger either. Previous explicit
    # user caps may be referenced when explaining a reduction, e.g. 45k -> 30k.
    allowed_totals = {data.get("budget_lkr")}
    for turn in history or []:
        if turn.get("role") == "user":
            previous = extract_budget_cap(turn.get("content", ""))
            if previous is not None:
                allowed_totals.add(previous)
    if any(amount not in allowed_totals for amount in _display_amounts(data["summary"])):
        issues.append(
            "Summary contains separate price figures that are not the trip total. "
            "Describe the compromises without duplicating category or item prices; "
            "the server provides the estimated budget breakdown."
        )
    return issues


def attach_budget_details(data: dict, budget_cap_lkr: Optional[int]) -> dict:
    result = dict(data)
    # Never carry a model-generated or stale breakdown into the response.
    result.pop("budget_breakdown", None)
    if budget_cap_lkr is not None:
        result["budget_cap_lkr"] = budget_cap_lkr
    total, days = result.get("budget_lkr"), result.get("days")
    if result.get("itinerary") and type(total) is int and total > 0 and type(days) is int and days > 0:
        allocation = calculate_budget(days, total, travel_style="budget")
        result["budget_breakdown"] = {
            **allocation["breakdown_lkr"], "total_lkr": total, "is_estimate": True,
        }
    return result
