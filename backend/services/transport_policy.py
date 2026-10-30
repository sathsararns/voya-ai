"""No route-specific travel-time guarantees from generic guide snippets."""

import re

TRANSPORT_INSTRUCTION = (
    "No verified route-duration or direct-service data is supplied for itinerary "
    "transport. Do not guess numeric train/bus/drive/transfer durations, even with "
    "'approximately'. A Colombo journey duration cannot be reused for a Galle "
    "journey. Describe travel without numeric durations and say to confirm the "
    "route, connections and timing with the operator. Do not promise direct or "
    "non-stop trains. Timetable departure times, when specifically requested and "
    "supported by the supplied dated notice, are different from journey durations.\n"
)

_DURATION = re.compile(
    r"\b(?:\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|half)"
    r"(?:\s*[-–]\s*\d+(?:\.\d+)?)?\s*[-‑]?\s*(?:hours?|hrs?|h|minutes?|mins?|min)\b",
    re.I,
)
_TRANSPORT = re.compile(r"\b(?:train|rail|railway|bus|drive|driving|transfer|journey|travel|transit)\b", re.I)


def is_transport_duration_question(message: str) -> bool:
    return bool(_TRANSPORT.search(message) and re.search(
        r"\b(?:how long|journey time|travel time|duration|how many (?:hours|minutes))\b", message, re.I,
    ))


def validate_transport_claims(data: dict) -> list[str]:
    if not data.get("itinerary"):
        return []
    texts = [data.get("summary", "")]
    for day in data["itinerary"]:
        texts.extend([day["title"], *day["items"]])
    issues = []
    for text in texts:
        route_duration = False
        for duration in _DURATION.finditer(text):
            activity = re.search(r"\b(?:spend|enjoy|reserve|set aside)\s*$", text[:duration.start()], re.I)
            on_transport = re.match(r"\s*(?:(?:on|in|for)\s+(?:the\s+|a\s+)?)?(?:train|bus|driving|travell?ing)\b",
                                    text[duration.end():], re.I)
            if _TRANSPORT.search(text) and (not activity or on_transport):
                route_duration = True
                break
        if route_duration:
            issues.append(
                f"Unsupported route-specific transport duration in {text!r}. "
                "No verified journey durations are supplied. Remove the numeric "
                "duration and ask the traveller to confirm timing/connections with the operator."
            )
        if re.search(r"\b(?:direct|non[- ]?stop)\b.{0,35}\b(?:train|rail)\b"
                     r"|\b(?:train|rail)\b.{0,35}\b(?:direct|non[- ]?stop)\b", text, re.I):
            issues.append("Direct train service has not been verified. Do not promise it; confirm the route and connections with the operator.")
    return issues
