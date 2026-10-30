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
    "Do not call a generated estimate the user's usual, saved or stated budget. "
    "Only an explicitly supplied trip cap is a stated budget. A saved budget "
    "preference is advisory and requires confirmed account preference data. "
    "Otherwise label any proposed total as a suggested planning estimate.\n"
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


def saved_budget_range(preferences=None):
    preferences = preferences or {}
    if preferences.get("budget_preference_source") != "user_provided":
        return None
    lower, upper = preferences.get("budget_lkr_min"), preferences.get("budget_lkr_max")
    if lower is None:
        lower = upper
    if upper is None:
        upper = lower
    if type(lower) is not int or type(upper) is not int or not 0 <= lower <= upper:
        return None
    return lower, upper


def validate_budget_origin(data: dict, budget_cap_lkr=None, preferences=None) -> list[str]:
    if not data.get("itinerary"):
        return []
    summary = data.get("summary", "")
    saved = saved_budget_range(preferences)
    saved_pattern = (
        r"\b(?:your|the user's)\s+(?:(?:saved|usual|typical|normal|regular|preferred|previous|past)\s+)+(?:total\s+)?budget"
        r"|\b(?:saved|usual|typical|normal)\s+budget(?:\s+range|\s+preference)?\b"
    )
    def positive_claim(pattern):
        for match in re.finditer(pattern, summary, re.I):
            clause = re.split(r"[.!?;,\n]", summary[max(0, match.start() - 90):match.start()])[-1]
            if re.search(r"\b(?:no|not|without|unknown|unverified|haven't|hasn't|isn't|wasn't)\b", clause, re.I):
                continue
            if re.match(r"\s+(?:(?:is|was|has|been)\s+)*(?:not|unknown|unverified)\b", summary[match.end():], re.I):
                continue
            return True
        return False
    saved_claim = positive_claim(saved_pattern)
    if saved_claim and saved is None:
        return ["There is no confirmed saved budget preference. Do not claim this estimate is the user's usual or saved budget; label it as a suggested estimate."]
    if saved_claim and saved is not None:
        total = data.get("budget_lkr")
        if type(total) is int and not saved[0] <= total <= saved[1]:
            return [f"The proposed total is outside the confirmed saved budget range {saved}; do not claim it fits that range."]
    if budget_cap_lkr is None and not saved_claim and positive_claim(
        r"\b(?:your|the user's)\s+(?:(?:stated|given|provided|specified|total)\s+)*"
        r"(?:(?:LKR|Rs\.?)\s*[\d,]+\s+)?budget\b"
    ):
        return ["No user-stated budget exists for this trip. Present the total as a suggested estimate, not 'your budget'."]
    return []


def attach_budget_details(data: dict, budget_cap_lkr: Optional[int], *, preferences=None) -> dict:
    result = dict(data)
    # Never carry a model-generated or stale breakdown into the response.
    result.pop("budget_breakdown", None)
    result.pop("budget_source", None)
    result.pop("budget_note", None)
    result.pop("budget_cap_lkr", None)
    if budget_cap_lkr is not None:
        result["budget_cap_lkr"] = budget_cap_lkr
    total, days = result.get("budget_lkr"), result.get("days")
    if result.get("itinerary") and type(total) is int and total > 0 and type(days) is int and days > 0:
        allocation = calculate_budget(days, total, travel_style="budget")
        if (result.get("planning_context") or {}).get("nights") == 0:
            # A day trip needs no overnight allowance. Rescale the remaining
            # planning categories, preserving the exact total and buffer.
            weights = BREAKDOWN_PERCENTAGES_BY_STYLE["budget"]
            remaining = 100 - weights["accommodation"]
            breakdown = {f"{key}_lkr": (0 if key == "accommodation" else total * weight // remaining)
                         for key, weight in weights.items()}
            breakdown["misc_lkr"] += total - sum(breakdown.values())
            allocation["breakdown_lkr"] = breakdown
        result["budget_breakdown"] = {
            **allocation["breakdown_lkr"], "total_lkr": total, "is_estimate": True,
        }
        saved = saved_budget_range(preferences)
        if budget_cap_lkr is not None:
            result.update(budget_source="user_provided",
                          budget_note=f"Your stated total budget cap is LKR {budget_cap_lkr:,}. Category amounts are planning allowances, not verified quotes.")
        elif saved and saved[0] <= total <= saved[1]:
            saved_label = f"LKR {saved[0]:,}" if saved[0] == saved[1] else f"LKR {saved[0]:,}–{saved[1]:,}"
            result.update(budget_source="saved_preference",
                          budget_note=f"Uses your saved budget preference ({saved_label}). Confirm this trip's spending limit; these are planning allowances, not verified quotes.")
        else:
            result.update(budget_source="estimate",
                          budget_note="Suggested planning estimate; please confirm your total budget. Category amounts are allowances, not verified quotes.")
    return result
