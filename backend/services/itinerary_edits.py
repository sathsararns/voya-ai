"""Verify requested additions against the actual previous itinerary.

Rephrased activities and promises in a summary are not proof that a new
attraction was added. Named venues use the sourced catalogue's aliases.
"""

import json
import re

from services.venue_facts import VENUE_FACTS

_ADDITION = re.compile(
    r"\b(?:add|include)\s+(?P<count>one|two|three|[1-3]|a|an|another)\s+"
    r"(?:(?:more|extra|additional|new)\s+)?"
    r"(?:(?P<category>cultural|historical|historic|heritage)\s+)?"
    r"(?P<kind>attractions?|sites?|stops?|museums?|church(?:es)?)\b", re.IGNORECASE,
)
_COUNTS = {"one": 1, "two": 2, "three": 3, "a": 1, "an": 1, "another": 1,
           "1": 1, "2": 2, "3": 3}


def _mentions(text: str, terms) -> bool:
    text = " ".join(text.split())
    return any(re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE) for term in terms)


def _previous_plan(history):
    for turn in reversed(history or []):
        if turn.get("role") != "assistant":
            continue
        try:
            reply = json.loads(turn.get("content", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(reply, dict) and isinstance(reply.get("itinerary"), list) and reply["itinerary"]:
            return reply
    return None


def _activity_text(plan, include_titles=True):
    texts = []
    for day in plan.get("itinerary") or []:
        if isinstance(day, dict):
            if include_titles:
                texts.append(str(day.get("title") or ""))
            texts.extend(item for item in (day.get("items") or []) if isinstance(item, str))
    return " ".join(texts)


def _addition_context(message, history):
    match = _ADDITION.search(message)
    previous = _previous_plan(history)
    if not match or previous is None:
        return None
    if re.search(r"\b(?:not|don't|never)\s*$", message[:match.start()], re.IGNORECASE):
        return None
    old_text = _activity_text(previous)
    visited = {venue["name"] for venue in VENUE_FACTS if _mentions(old_text, venue["aliases"])}
    kind = match["kind"].lower()
    candidates = [
        venue for venue in VENUE_FACTS
        if venue["name"] not in visited
        and _mentions(old_text + " " + str(previous.get("destination") or ""), venue["region_terms"])
        and (not kind.startswith("museum") or venue["kind"] == "museum")
        and (not kind.startswith("church") or venue["kind"] == "church")
    ]
    return {"count": _COUNTS[match["count"].lower()], "previous": previous,
            "visited": visited, "candidates": candidates}


def addition_prompt_context(message: str, history=None) -> str:
    context = _addition_context(message, history)
    if context is None:
        return ""
    lines = [
        f"The user requested exactly {context['count']} additional named attraction(s). "
        "Actually add new visits in itinerary items; repeating or rewording the "
        "previous plan does not satisfy this request. Preserve its existing "
        "visits and duration unless the user explicitly changes them. "
        "Keep the current total budget cap and do not invent free admission.",
        "Sourced candidates in the already-planned areas that were not in the previous itinerary:",
    ]
    lines.extend(f"- {venue['name']}: {venue['location']}. Source: {venue['source']}"
                 for venue in context["candidates"])
    if not context["candidates"]:
        lines.append("No new catalogue candidate is available. Ask which attraction the user wants; "
                     "do not claim to have added one while returning the unchanged itinerary.")
    return "\n".join(lines) + "\n"


def validate_attraction_addition(message: str, data: dict, history=None) -> list[str]:
    context = _addition_context(message, history)
    if context is None or not data.get("itinerary"):
        return []
    new_text = _activity_text(data, include_titles=False)
    added = [venue for venue in context["candidates"] if _mentions(new_text, venue["aliases"])]
    issues = []
    if len(added) != context["count"]:
        suggestions = ", ".join(venue["name"] for venue in context["candidates"])
        issues.append(
            f"The requested addition is missing or has the wrong count: add exactly {context['count']} genuinely new "
            "named attraction(s) to the itinerary items, not only the summary. "
            f"Previously visited venues and their aliases do not count. New sourced choices: "
            f"{suggestions or 'none; ask the user which attraction to add'}."
        )
    for venue in VENUE_FACTS:
        if venue["name"] not in context["visited"] or _mentions(new_text, venue["aliases"]):
            continue
        removed_explicitly = any(re.search(
            r"\b(?:remove|skip|omit|exclude|drop)\b[^.!?]{0,80}\b" + re.escape(alias) + r"\b",
            message, re.IGNORECASE,
        ) for alias in venue["aliases"])
        if not removed_explicitly:
            issues.append(f"Adding an attraction must not silently remove the existing {venue['name']} visit.")
    explicit_days = re.search(r"\b(\d+)\s*-?\s*days?\b", message, re.IGNORECASE)
    expected_days = int(explicit_days[1]) if explicit_days else context["previous"].get("days")
    if type(expected_days) is int and expected_days > 0:
        day_numbers = [day["day"] for day in data["itinerary"]]
        if data.get("days") != expected_days or day_numbers != list(range(1, expected_days + 1)):
            issues.append(f"Keep exactly {expected_days} itinerary days while adding the attraction.")
    return issues
