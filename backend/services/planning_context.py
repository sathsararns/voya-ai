"""Server-owned planning assumptions, separated from user-stated facts."""

import json
import re

from services.context_router import DESTINATIONS, is_options_request
from services.answer_policy import non_trip_answer

_PLACES = sorted(DESTINATIONS | {"maradana", "colombo fort", "peradeniya"}, key=len, reverse=True)
_PLACE = "(?:" + "|".join(map(re.escape, _PLACES)) + ")"
_STOP = r"(?=\s+(?:to|for|on|with|using|and|but|budget|next|tomorrow|this)\b|[,.;!?\n]|$)"
_ORIGIN_PATTERNS = [
    r"\b(?:starting point|departure point|origin)\s*(?:is|:|=)\s*(?P<place>[A-Za-z][A-Za-z '-]{1,65}?)" + _STOP,
    r"\b(?:start(?:ing)?|depart(?:ing)?|leav(?:e|ing)|travell?ing)\s+(?:from|in|at)\s+(?P<place>[A-Za-z][A-Za-z '-]{1,65}?)" + _STOP,
    r"\b(?:i am|i'm|we are|we're)\s+(?:currently\s+)?(?:in|based in)\s+(?P<place>[A-Za-z][A-Za-z '-]{1,65}?)" + _STOP,
    rf"\bfrom\s+(?P<place>{_PLACE})\b",
]
_NUMBER = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_NUMBERS = dict(zip("one two three four five six seven eight nine ten".split(), range(1, 11)))


def _number(value: str) -> int:
    return _NUMBERS[value.lower()] if value.lower() in _NUMBERS else int(value)


def stated_origin(text: str):
    candidates = []
    for pattern in _ORIGIN_PATTERNS:
        for match in re.finditer(pattern, text, re.I):
            prefix = text[max(0, match.start() - 30):match.start()]
            if re.search(r"\b(?:not|don't|do not|never)\s*$", prefix, re.I):
                continue
            value = " ".join(match["place"].strip().split())
            if value.lower() in {"my saved preferences", "home", "there", "here", "scratch", "my preferences",
                                 "unknown", "undecided", "not decided", "not sure", "not provided", "not set"}:
                continue
            candidates.append((match.start(), value.title()))
    return max(candidates)[1] if candidates else None


def _stated_party(text: str):
    counts = [match for match in re.finditer(rf"\b(?P<n>{_NUMBER})\s+(?:adults?|children|child|kids?|people|persons?|travell?ers?)\b", text, re.I)
              if not re.search(r"\b(?:not|instead of)\s*$", text[max(0, match.start() - 20):match.start()], re.I)]
    if counts:
        # Count a party such as "2 adults and 1 child", rather than dropping children.
        total = _number(counts[-1]["n"])
        following = counts[-1]
        for previous in reversed(counts[:-1]):
            if not re.fullmatch(r"\s*(?:and|,|\+|&)\s*", text[previous.end():following.start()], re.I):
                break
            total += _number(previous["n"])
            following = previous
        return total
    family = re.search(rf"\bfamily of\s+(?P<n>{_NUMBER})\b", text, re.I)
    if family:
        return _number(family["n"])
    if re.search(r"\b(?:solo|alone|just me|by myself)\b", text, re.I):
        return 1
    if re.search(r"\b(?:a couple|two of us)\b", text, re.I):
        return 2
    return None


def planning_context(message: str, history=None, days=None) -> dict:
    facts = {}
    unknown = set()
    # Trust only user-sourced fields in prior server metadata. An assumed
    # Colombo departure must never become a claimed user-stated origin.
    for turn in [*(history or []), {"role": "user", "content": message}]:
        if turn.get("role") == "assistant":
            try:
                previous = json.loads(turn.get("content", ""))
            except (ValueError, TypeError):
                continue
            context = previous.get("planning_context") if isinstance(previous, dict) else None
            if isinstance(context, dict):
                for key in ("starting_point", "travellers", "nights", "dates"):
                    if context.get(key + "_source") == "user" and context.get(key) is not None:
                        facts[key] = context[key]
                        unknown.discard(key)
                    elif context.get(key + "_source") == "unknown":
                        facts.pop(key, None)
                        unknown.add(key)
            continue
        if turn.get("role") != "user":
            continue
        text = turn.get("content", "")
        if is_options_request(text):
            continue
        origin = stated_origin(text)
        if origin:
            facts["starting_point"] = origin
        party = _stated_party(text)
        if party is not None and party > 0:
            facts["travellers"] = party
        nights = re.search(rf"\b(?P<n>{_NUMBER})\s+nights?\b", text, re.I)
        if nights:
            facts["nights"] = _number(nights["n"])
        date = re.search(
            r"\b20\d{2}-\d{2}-\d{2}\b(?:\s*(?:to|until|through)\s*20\d{2}-\d{2}-\d{2}\b)?"
            r"|\b(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,4}(?:,?\s+20\d{2})?\b"
            r"|\b(?:tomorrow|next weekend|next week|next month|this weekend)\b", text, re.I,
        )
        if date:
            facts["dates"] = date.group()
        if re.search(r"\b(?:do not|don't)\s+(?:assume|guess)|\bask me (?:first|before)\b", text, re.I):
            unknown.update({"starting_point", "travellers", "nights", "dates"} - facts.keys())
        if re.search(r"\b(?:starting point|origin)\s+(?:is\s+)?(?:unknown|undecided|not decided|not set)\b", text, re.I):
            facts.pop("starting_point", None)
            unknown.add("starting_point")
    if days is None:
        match = re.search(r"\b(\d{1,2})\s*-?\s*days?\b", message, re.I)
        days = int(match[1]) if match else None
    defaults = {"starting_point": "Colombo", "travellers": 1,
                "nights": max(days - 1, 0) if type(days) is int else None, "dates": None}
    result = {}
    for key, default in defaults.items():
        result[key] = facts.get(key, None if key in unknown else default)
        result[key + "_source"] = "user" if key in facts else ("assumed" if result[key] is not None else "unknown")
    return result


def planning_prompt_context(message: str, history=None) -> str:
    if non_trip_answer(message) or is_options_request(message):
        return ""
    context = planning_context(message, history)
    return (
        "\nPlanning facts and provisional assumptions (server-owned): " + json.dumps(context) + ". "
        "Use user-stated values exactly. Assumed values are defaults, not saved preferences or "
        "facts the user provided; the UI will disclose them. Start arrival travel from the stated/assumed "
        "starting point. When it is unknown, plan local activities and ask for the starting point; "
        "do not invent an origin. Do not promise current train times or availability for unspecified "
        "dates. Use the stated traveller count and nights; no overnight check-in for zero nights.\n"
    )


def attach_planning_context(data: dict, message: str, history=None) -> dict:
    result = dict(data)
    result.pop("assumptions", None)
    result.pop("planning_context", None)
    if not data.get("itinerary"):
        return result
    context = planning_context(message, history, data.get("days"))
    assumptions = []
    if context["starting_point_source"] == "assumed":
        assumptions.append(f"Starting point: {context['starting_point']} (assumed; please confirm).")
    elif context["starting_point_source"] == "unknown":
        assumptions.append("Starting point not provided; confirm it before arranging arrival transport.")
    if context["travellers_source"] == "assumed":
        assumptions.append(f"Travellers: {context['travellers']} (assumed).")
    elif context["travellers_source"] == "unknown":
        assumptions.append("Number of travellers not provided; confirm the party size.")
    if context["nights_source"] == "assumed":
        nights = context["nights"]
        assumptions.append(f"Overnight stays: {nights} {'night' if nights == 1 else 'nights'} (assumed for this trip).")
    elif context["nights_source"] == "unknown":
        assumptions.append("Number of overnight stays not provided; confirm accommodation needs.")
    if context["dates_source"] == "unknown":
        assumptions.append("Travel dates are not set; this is a provisional itinerary.")
    result.update(planning_context=context, assumptions=assumptions)
    return result


def validate_planning_context(message: str, data: dict, history=None) -> list[str]:
    if not data.get("itinerary"):
        return []
    context = planning_context(message, history, data.get("days"))
    issues = []
    # Check the first explicit departure, not later transfers between stops.
    for item in data["itinerary"][0]["items"]:
        if re.search(r"\b(?:train|bus|travel|depart|drive|leave|flight|transfer)\b", item, re.I):
            origin = stated_origin(item)
            if origin:
                expected = context["starting_point"]
                same_origin = expected is not None and (
                    origin.lower() == expected.lower()
                    or (expected.lower() == "colombo" and origin.lower() == "colombo fort")
                )
                if not same_origin:
                    issues.append(f"First departure uses {origin}, but the planning starting point is {expected or 'unknown'}. Use the provided origin, or ask for it without inventing arrival travel.")
                break
    if context["nights"] == 0:
        for day in data["itinerary"]:
            if any(re.search(r"\b(?:check[- ]in|overnight stay)\b", item, re.I) for item in day["items"]):
                issues.append("The trip has zero overnight stays; remove overnight check-in activities.")
                break
    return issues
