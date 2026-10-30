"""Small, conservative evidence gate shared by chat and retrieval tools.

Similarity is a ranking signal, not answer confidence. Require lexical or
named-venue support as well, and keep unsupported snippets out of citations.
"""

import json
import math
import os
import re

from services.context_router import DESTINATIONS, _contains_any
from services.venue_facts import VENUE_FACTS

STOPWORDS = set("""a an the i me my we our you your it its is are was be been do
does did can could should would will what which who why how when where tell
about give explain please know need want like makes famous special city
visit visiting travel traveling travelling trip plan planning sri lanka lankan
in on at to from for of with and or as by this that these those same keep
make more instead day days second first third relaxed include including
current latest today now fee fees entrance entry admission price prices cost
costs opening hours time times attraction attractions museum garden national
fort historic cultural tourist top information options used work getting
around island much also total budget meals vegetarian food-friendly""".split())


def normalized(text: str) -> str:
    # Rejoin PDF line-break hyphenation before tokenising.
    return " ".join(re.sub(r"-\s*\n\s*", "", text.lower()).split())


def terms(text: str) -> set[str]:
    aliases = {"rail": "train", "railway": "train", "railways": "train", "trains": "train",
               "tuktuks": "tuktuk", "mosquitos": "mosquito", "mosquitoes": "mosquito"}
    return {aliases.get(word, word.rstrip("s")) for word in re.findall(r"[^\W\d_]+", normalized(text))
            if len(word) > 2 and word not in STOPWORDS}


def build_search_query(message: str, history=None) -> str:
    """Resolve destination-less follow-ups using the latest actual itinerary."""
    if _contains_any(message.lower(), DESTINATIONS):
        return message
    if not re.search(r"\b(?:it|this|that|same|instead|day|second|first|third|add|change|relaxed)\b",
                     message, re.I):
        return message
    for turn in reversed(history or []):
        if turn.get("role") != "assistant":
            continue
        try:
            data = json.loads(turn.get("content", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or not data.get("itinerary"):
            continue
        destination = data.get("destination")
        if isinstance(destination, str) and destination.strip():
            return f"{message}\nDestination: {destination[:200]}"
    return message


def named_venue_phrases(query: str) -> list[str]:
    """Anchor named-place questions on the actual name, not prompt instructions.

    For example "do not invent a place" must not make an unrelated paragraph
    containing "place" qualify as evidence for Atlantis Moon Museum.
    """
    matches = re.findall(
        r"\b(?:of|for|at|about|visit|visiting)\s+(?:the\s+)?"
        r"([\w'-]+(?:\s+[\w'-]+){0,5}\s+(?:museum|gardens?|temple|tower|fort))\b",
        query, re.I,
    )
    generic_names = {"the", "a", "an", "of", "temple", "museum", "national", "royal", "garden", "gardens", "tower", "fort"}
    return [normalized(match) for match in matches
            if set(re.findall(r"\w+", match.lower())) - generic_names]


def relevant_hits(query: str, hits: list[dict], limit: int) -> list[dict]:
    try:
        minimum = float(os.getenv("KB_MIN_SCORE", "0.25"))
    except ValueError:
        minimum = 0.25
    if not math.isfinite(minimum) or not -1 <= minimum <= 1:
        minimum = 0.25
    query_text = normalized(query)
    venues = [venue for venue in VENUE_FACTS
              if any(alias in query_text for alias in venue["aliases"])]
    named_places = named_venue_phrases(query)
    query_terms = terms(query)
    timetable_question = bool(re.search(r"\b(?:timetable|departures?|train times?|what time.{0,30}train)\b", query, re.I))
    stations = sorted((DESTINATIONS - {"sri lanka", "sri lankan"}) | {"maradana", "colombo fort", "beliatta", "kalutara south",
                      "wadduwa", "panadura", "moratuwa", "ambepussa", "mirigama"}, key=len, reverse=True)
    station_pattern = "(?:" + "|".join(map(re.escape, stations)) + ")"
    route = re.search(rf"\b({station_pattern})\s+(?:to|→|->)\s+({station_pattern})\b", query_text)
    origin_request = re.search(rf"\bfrom\s+({station_pattern})\b", query_text)
    destination_request = re.search(rf"\bto\s+({station_pattern})\b", query_text)
    mentioned = []
    for station in stations:
        if re.search(r"\b" + re.escape(station) + r"\b", query_text) and not any(
            re.search(r"\b" + re.escape(station) + r"\b", longer) for longer in mentioned
        ):
            mentioned.append(station)
    def station_matches(requested, actual):
        return requested == actual or (requested == "colombo" and actual == "colombo fort")
    seen = set()
    accepted = []
    for hit in sorted(hits, key=lambda h: h.get("score") or 0, reverse=True):
        fields = hit.get("fields", {})
        text = normalized(fields.get("text") or "")
        score = hit.get("score")
        if not text or not isinstance(score, (int, float)) or not math.isfinite(score) or score < minimum:
            continue
        if fields.get("route_origin") and not timetable_question:
            # Preferring train travel is not a request for departure times.
            # A coastal timetable must not become evidence for a Kandy plan.
            continue
        if timetable_question:
            # Journey-duration paragraphs and reversed routes cannot support
            # departure times. Only reviewed, direction-labelled rows can.
            if fields.get("extraction_method") != "reviewed_transcription" or not fields.get("route_origin"):
                continue
            origin = normalized(fields.get("route_origin", ""))
            destination = normalized(fields.get("route_destination", ""))
            if route and (not station_matches(route[1], origin) or not station_matches(route[2], destination)):
                continue
            if not route:
                if origin_request and not station_matches(origin_request[1], origin):
                    continue
                if destination_request and not station_matches(destination_request[1], destination):
                    continue
                if not all(station_matches(station, origin) or station_matches(station, destination) for station in mentioned):
                    continue
        if venues:
            # A generic tickets paragraph cannot support a named venue's fee.
            if not any(any(alias in text for alias in venue["aliases"]) for venue in venues):
                continue
        elif named_places:
            if not any(name in text for name in named_places):
                continue
        elif not query_terms or not query_terms.intersection(terms(text)):
            continue
        if text in seen:
            continue
        seen.add(text)
        accepted.append(hit)
        if len(accepted) >= limit:
            break
    return accepted
