"""Prepare deterministic tool context without spending an LLM request."""

import json
import re

from services.budget import resolve_budget_cap
from services.context_router import is_options_request
from services.tools import execute_tool_call, SRI_LANKA_CLIMATE_REFERENCE
from services.trip_constraints import stated_days
from services.trip_state import last_plan


def local_tool_context(message, candidates, history=None):
    # Options contain several unrelated durations/destinations. Never turn
    # them into one fabricated budget or weather request.
    if not candidates or is_options_request(message):
        return "", []
    previous = last_plan(history)
    days = stated_days(message)
    if days is None and previous:
        days = previous.get("days")
    results, used = [], []
    cap = resolve_budget_cap(message, history)
    if "calculate_budget" in candidates and days and cap is not None and cap > 0:
        result = execute_tool_call("calculate_budget", {"days": days, "total_budget_lkr": cap})
        if not result.get("error"):
            # The final ledger is server-calculated after validation. Do not
            # duplicate a second category table in the input prompt.
            results.append({"tool": "calculate_budget", "days": days,
                            "total_budget_lkr": result["total_budget_lkr"],
                            "note": "Total trip cap, not per person. Allowances are not price quotes."})
            used.append("calculate_budget")
    if "get_weather" in candidates:
        # Current explicit places win. For an ambiguous option selection or
        # missing place, skip instead of guessing a saved favourite/origin.
        text = message
        if not re.search(r"\b(?:option|choose|choice|second|third|fourth)\b", text, re.I):
            places = [place for place in SRI_LANKA_CLIMATE_REFERENCE
                      if re.search(r"\b" + re.escape(place) + r"\b", text, re.I)]
            if not places and previous:
                destination = previous.get("destination") or ""
                places = [place for place in SRI_LANKA_CLIMATE_REFERENCE
                          if re.search(r"\b" + re.escape(place) + r"\b", destination, re.I)]
            for place in places[:4]:
                result = execute_tool_call("get_weather", {"destination": place.title(), "days": days})
                if not result.get("error"):
                    results.append({"tool": "get_weather", "result": result})
                    if "get_weather" not in used:
                        used.append("get_weather")
    # search_places / generate_itinerary would repeat the KB search already
    # performed by the prompt builder. Its filtered evidence is reused there.
    if not results:
        return "", []
    return "Local tool results (planning allowances and seasonal references, not live quotes):\n" + json.dumps(
        results, ensure_ascii=False, separators=(",", ":")), used
