"""Trip instructions still apply when a caller opts out of preference learning."""

import re

_MANAGEMENT = re.compile(
    r"\b(?:do not|don't|never)\s+(?:update|change|overwrite)\b[^.!?\n]{0,100}"
    r"\b(?:saved|usual|preferences?|profile|memory)\b[^.!?\n]*"
    r"|\b(?:do not|don't|never)\s+(?:save|store|remember|learn)\b[^.!?\n]*", re.I,
)


def planning_text(message: str) -> str:
    # "Do not update my saved budget preference" is not an itinerary edit.
    return _MANAGEMENT.sub("", message)


def learning_disabled(message: str) -> bool:
    return bool(_MANAGEMENT.search(message) or re.search(
        r"\b(?:for|on)\s+this\s+trip\s+only\b|\b(?:just|only)\s+for\s+this\s+trip\b"
        r"|\bnot\s+my\s+usual\s+budget\b", message, re.I,
    ))


def learning_requested(message: str) -> bool:
    return not learning_disabled(message) and bool(re.search(
        r"\b(?:save|remember|update|learn)\b[^.!?\n]{0,90}\b(?:preferences?|usual|future trips|profile)\b",
        message, re.I,
    ))
