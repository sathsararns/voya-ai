"""Catch close spelling drift for a requested/selected destination.

This is a conservative consistency check, not a worldwide place gazetteer.
Never autocorrect a place explicitly named by the user or another known city.
"""

import re


def _one_edit(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        differences = [n for n, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(differences) == 1 or (
            len(differences) == 2 and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    shorter, longer = sorted((left, right), key=len)
    return any(longer[:n] + longer[n + 1:] == shorter for n in range(len(longer)))


def validate_destination_spelling(message: str, data: dict, known_destinations, history=None) -> list[str]:
    if not data.get("itinerary"):
        return []
    user_text = " ".join([message, *[turn.get("content", "") for turn in history or []
                                    if turn.get("role") == "user"]]).lower()
    selected = user_text + " " + str(data.get("destination") or "").lower()
    expected = [name for name in known_destinations if len(name) >= 5
                and re.search(r"\b" + re.escape(name) + r"\b", selected)]
    known = set(known_destinations)
    fields = [("summary", data.get("summary", ""), False),
              ("destination", data.get("destination", ""), True)]
    for day in data["itinerary"]:
        fields.append((f"Day {day['day']} title", day["title"], True))
        fields.extend((f"Day {day['day']} item", text, False) for text in day["items"])
    issues = []
    for label, text, title in fields:
        for canonical in expected:
            count = len(canonical.split())
            for match in re.finditer(r"\b[A-Za-z]+(?:\s+[A-Za-z]+){" + str(count - 1) + r"}\b", text or ""):
                candidate = match.group().lower()
                if candidate in known or re.search(r"\b" + re.escape(candidate) + r"\b", user_text):
                    continue
                if candidate[:2] != canonical[:2] or not _one_edit(candidate, canonical):
                    continue
                place_context = re.search(
                    r"\b(?:to|from|in|around|through|towards|visit|explore)\s+(?:the\s+)?$",
                    text[max(0, match.start() - 45):match.start()], re.I,
                )
                if place_context or title:
                    issues.append(
                        f"{label} has a likely destination spelling error: {match.group()!r} "
                        f"for the selected destination {canonical.title()!r}. Use the correct "
                        "destination consistently; do not change the route to a different place."
                    )
    return list(dict.fromkeys(issues))
