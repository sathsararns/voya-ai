"""Small, sourced venue catalogue for itinerary placement checks.

This verifies only the listed venues' locations, not all destinations or
live road travel times. Prices and opening hours are deliberately omitted.
"""

import re
from services.trip_state import scoped_trip_history

VENUE_FACTS = (
    {
        "name": "Brief Garden",
        "aliases": ("brief garden", "brief gardens"),
        "region_terms": ("bentota", "beruwala", "beruwela", "kalawila", "aluthgama"),
        "location": "Kalawila village, Beruwala, near Bentota",
        "source": "https://www.briefgarden.com/home",
        "kind": "garden",
    },
    {
        "name": "Galle National Museum",
        "aliases": ("galle national museum", "national museum of galle", "national museum in galle"),
        "region_terms": ("galle",),
        "location": "inside Galle Fort, Galle",
        "source": "https://www.srilanka.travel/national-museum",
        "kind": "museum",
    },
    {
        "name": "Dutch Reformed Church",
        "aliases": ("dutch reformed church", "groote kerk"),
        "region_terms": ("galle",),
        "location": "Galle Fort, Galle",
        "source": "https://www.srilanka.travel/ArrivebyaCruise/galle-port.php",
        "kind": "church",
    },
    {
        "name": "Galle Maritime Museum",
        "aliases": ("galle maritime museum", "national maritime museum", "maritime museum"),
        "region_terms": ("galle",),
        "location": "inside Galle Fort, Galle",
        "source": "https://www.srilanka.travel/national-museum",
        "kind": "museum",
    },
)


def _mentions(text: str, terms) -> bool:
    text = " ".join(text.split())
    return any(re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE) for term in terms)


def venue_location_context() -> str:
    lines = ["Sourced venue locations (location facts only; fees remain unverified):"]
    for venue in VENUE_FACTS:
        lines.append(f"- {venue['name']}: {venue['location']}. Source: {venue['source']}")
    lines.append(
        "Place each venue on its local area's day. Brief Garden belongs on the "
        "Bentota/Beruwala day, not a Galle or Mirissa sightseeing day. A day trip "
        "from another city must explicitly describe the transfer to the venue's "
        "actual area. Do not invent branches such as a 'Brief Garden extension'. "
        "Keep user-requested venues when revising a plan unless the user removes them."
    )
    return "\n".join(lines) + "\n"


def validate_venue_placement(data: dict, known_destinations) -> list[str]:
    issues = []
    for venue in VENUE_FACTS:
        alias_pattern = "(?:" + "|".join(re.escape(alias) for alias in venue["aliases"]) + ")"
        for place in known_destinations:
            if place in venue["region_terms"]:
                continue
            wrong_association = (
                rf"\b{alias_pattern}\s+(?:(?:is|located|situated)\s+)*(?:in|near|at)\s+{re.escape(place)}\b"
                rf"|\b(?:sights|attractions|gardens|museums)\s+in\s+{re.escape(place)}\s*\([^)]*{alias_pattern}\b"
            )
            if re.search(wrong_association, data.get("summary", ""), re.IGNORECASE):
                issues.append(
                    f"Summary mislocates {venue['name']}: it is {venue['location']}, "
                    f"not in {place}. Source: {venue['source']}"
                )
        for day in data.get("itinerary") or []:
            texts = [day["title"], *day["items"]]
            relevant = [item for item in texts if _mentions(item, venue["aliases"])]
            if not relevant:
                continue
            title = day["title"]
            wrong_places = [place for place in known_destinations
                            if place not in venue["region_terms"] and _mentions(title, (place,))]
            for item in relevant:
                if venue["name"] == "Brief Garden" and re.search(
                    r"\bbrief gardens?\s+(?:extension|branch)\b", item, re.IGNORECASE,
                ):
                    issues.append("Do not invent a Brief Garden extension or branch; use the actual Beruwala venue.")
                    continue
                local_title = _mentions(title, venue["region_terms"])
                explicit_transfer = any(
                    _mentions(text, venue["region_terms"])
                    and re.search(r"\b(?:travel|transfer|drive|bus|taxi|journey|excursion|day trip)\b",
                                  text, re.IGNORECASE)
                    for text in texts
                )
                explicit_locality = not wrong_places and _mentions(item, venue["region_terms"])
                if not (local_title or explicit_transfer or explicit_locality):
                    issues.append(
                        f"Day {day['day']} ({title!r}) misplaces {venue['name']}. "
                        f"Its location is {venue['location']} ({venue['source']}). "
                        "Move it to that area's day, or explicitly describe the transfer "
                        "to its actual area; do not remove a requested venue."
                    )
    return issues


def validate_required_venues(message: str, data: dict, history=None) -> list[str]:
    if not data.get("itinerary"):
        return []
    history = scoped_trip_history(message, history)
    messages = [turn.get("content", "") for turn in history if turn.get("role") == "user"]
    messages.append(message)
    required = {}
    for text in messages:
        for venue in VENUE_FACTS:
            for alias in venue["aliases"]:
                for mention in re.finditer(r"\b" + re.escape(alias) + r"\b", text, re.IGNORECASE):
                    prefix = text[max(0, mention.start() - 100):mention.start()]
                    instructions = list(re.finditer(
                        r"\b(?P<action>include|visit|add|keep|retain|see|remove|skip|omit|exclude|drop|avoid)\b",
                        prefix, re.IGNORECASE,
                    ))
                    if instructions:
                        last = instructions[-1]
                        negated = re.search(r"\b(?:not|don't)\s*$", prefix[:last.start()], re.IGNORECASE)
                        required[venue["name"]] = (
                            last["action"].lower() in {"include", "visit", "add", "keep", "retain", "see"}
                            and not negated
                        )
    activity_text = " ".join(
        text for day in data["itinerary"] for text in [day["title"], *day["items"]]
    )
    return [
        f"Keep the user-requested {venue['name']} in the itinerary at its actual location "
        f"({venue['location']}); do not silently remove it when correcting the plan."
        for venue in VENUE_FACTS
        if required.get(venue["name"]) and not _mentions(activity_text, venue["aliases"])
    ]
