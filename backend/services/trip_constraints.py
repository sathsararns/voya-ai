"""Shared parsing for user-stated trip days and overnight stays."""

import re

_WORDS = dict(zip("zero one two three four five six seven eight nine ten".split(), range(11)))
NUMBER_PATTERN = r"(?:\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_JOIN = r"\s*[-\u2010-\u2014]?\s*"
DAY_COUNT_RE = re.compile(rf"\b(?P<n>{NUMBER_PATTERN}){_JOIN}days?\b", re.I)
NIGHT_COUNT_RE = re.compile(rf"\b(?P<n>{NUMBER_PATTERN}){_JOIN}(?:overnight\s+stays?|overnights?|nights?)\b", re.I)


def parse_number(value: str) -> int:
    return _WORDS[value.lower()] if value.lower() in _WORDS else int(value)


def _positive_matches(pattern, text):
    return [match for match in pattern.finditer(text)
            if not re.search(r"\b(?:not|instead of|rather than)\s*$", text[max(0, match.start() - 25):match.start()], re.I)]


def stated_days(text: str):
    matches = _positive_matches(DAY_COUNT_RE, text)
    if matches:
        return parse_number(matches[-1]["n"])
    if re.search(r"\bday[- ]trip\b", text, re.I):
        return 1
    return None


def stated_nights(text: str):
    matches = _positive_matches(NIGHT_COUNT_RE, text)
    if matches:
        return parse_number(matches[-1]["n"])
    if re.search(r"\b(?:no|without(?:\s+any)?)\s+(?:overnight(?:\s+stays?)?|nights?)\b", text, re.I):
        return 0
    return None
