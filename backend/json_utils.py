"""Turns messy LLM text output into the clean JSON dict the frontend expects.

Even with a JSON-only instruction (and Groq's JSON mode enabled), a model can
still wrap output in markdown fences, add stray prose, drift onto near-miss
field names, or get cut off mid-object by the token limit. This module is the
single place that repairs all of that before anything is saved or returned.
"""

import json
import re
from typing import Any, Dict, List, Optional

CHAT_RESPONSE_DEFAULTS: Dict[str, Any] = {
    "destination": None,
    "days": None,
    "budget_lkr": None,
    "summary": "",
    "itinerary": [],
    "follow_up_question": None,
}

# Field names models sometimes drift onto instead of the ones the frontend
# actually expects. Left side gets renamed to the right side, anywhere in
# the parsed object (including inside itinerary day entries).
FIELD_ALIASES = {
    "activities": "items",
    "activity": "items",
    "tasks": "items",
}


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> str:
    """Return the substring spanning the first balanced {...} object.

    Brace-counting (aware of strings, so braces inside quoted text don't
    throw it off) rather than a naive first-{-to-last-}, so leading/trailing
    prose around the JSON is dropped correctly even with nested objects.
    """
    start = text.find("{")
    if start == -1:
        return text

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        char = text[i]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    # Never closed — likely truncated by the token limit. Hand back what we
    # have from the opening brace onward for the repair step below.
    return text[start:]


def _close_unbalanced(text: str) -> str:
    """Best-effort close-out for JSON truncated mid-string/array/object.

    Only handles the common truncation shape (generation just stops partway
    through) — not a general-purpose JSON repairer.
    """
    in_string = False
    escape = False
    stack: List[str] = []

    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if stack:
                stack.pop()

    repaired = text
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def _rename_aliased_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {FIELD_ALIASES.get(k, k): _rename_aliased_fields(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_aliased_fields(item) for item in value]
    return value


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _coerce_itinerary(raw_itinerary: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_itinerary, list):
        return []

    days: List[Dict[str, Any]] = []
    for entry in raw_itinerary:
        if not isinstance(entry, dict):
            continue

        items = entry.get("items", [])
        if not isinstance(items, list):
            items = [items] if items else []

        day_number = _coerce_int(entry.get("day"))
        days.append(
            {
                "day": day_number if day_number is not None else len(days) + 1,
                "title": str(entry.get("title") or ""),
                "items": [str(item) for item in items if item is not None],
            }
        )
    return days


def normalize_chat_response(raw_text: str) -> Dict[str, Any]:
    """Turn raw model output into a dict that always matches ChatResponse.

    Always returns a valid, schema-shaped dict. Worst case (no JSON found
    at all) falls back to putting the raw text in `summary` rather than
    silently dropping it or crashing.
    """
    cleaned = _strip_code_fences(raw_text or "")
    candidate = _extract_json_object(cleaned)

    parsed: Optional[Dict[str, Any]] = None
    for attempt in (candidate, _close_unbalanced(candidate)):
        try:
            result = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict):
            parsed = result
            break

    if parsed is None:
        return {**CHAT_RESPONSE_DEFAULTS, "summary": cleaned or raw_text or ""}

    parsed = _rename_aliased_fields(parsed)

    data = dict(CHAT_RESPONSE_DEFAULTS)
    data.update(
        {
            "destination": _coerce_optional_str(parsed.get("destination")),
            "days": _coerce_int(parsed.get("days")),
            "budget_lkr": _coerce_int(parsed.get("budget_lkr")),
            "summary": str(parsed.get("summary") or ""),
            "itinerary": _coerce_itinerary(parsed.get("itinerary")),
            "follow_up_question": _coerce_optional_str(parsed.get("follow_up_question")),
        }
    )
    return data


_JSON_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
}


class SummaryStreamExtractor:
    """Incrementally decodes the `"summary"` string value out of a stream of
    raw JSON text fragments, as they arrive — before the JSON object as a
    whole is complete or even valid.

    The model (see services/groq_service.py) is prompted to return one JSON
    object matching the ChatResponse schema, so there is no plain-text
    reply to stream directly. `summary` is the one field that's actually
    shown to the user as conversational text, so this is a small state
    machine that watches the raw token stream for the literal `"summary"`
    key, then decodes characters of its string value one at a time
    (handling standard JSON escapes, including a \\uXXXX split across
    multiple feed() calls) until the closing quote — completely ignoring
    everything else in the JSON object (destination, itinerary, etc.),
    which is only ever needed once the full object is complete anyway.

    Feed raw fragments in arrival order via feed(); each call returns the
    newly-decoded substring available so far (often empty). Not reusable
    across two responses — construct a fresh instance per stream.
    """

    _KEY = '"summary"'

    def __init__(self) -> None:
        self._state = "seeking_key"
        self._key_tail = ""
        self._escape = False
        self._unicode_buffer = ""

    def feed(self, chunk: str) -> str:
        output: List[str] = []

        for ch in chunk:
            if self._state == "done":
                break

            if self._state == "seeking_key":
                self._key_tail = (self._key_tail + ch)[-len(self._KEY) :]
                if self._key_tail == self._KEY:
                    self._state = "seeking_colon"
                continue

            if self._state == "seeking_colon":
                if ch == ":":
                    self._state = "seeking_quote"
                continue

            if self._state == "seeking_quote":
                if ch == '"':
                    self._state = "in_string"
                elif not ch.isspace():
                    # Malformed/unexpected shape — give up rather than
                    # decode garbage.
                    self._state = "done"
                continue

            if self._state == "unicode_escape":
                self._unicode_buffer += ch
                if len(self._unicode_buffer) == 4:
                    try:
                        output.append(chr(int(self._unicode_buffer, 16)))
                    except ValueError:
                        pass
                    self._unicode_buffer = ""
                    self._escape = False
                    self._state = "in_string"
                continue

            # self._state == "in_string"
            if self._escape:
                if ch == "u":
                    self._state = "unicode_escape"
                    self._unicode_buffer = ""
                else:
                    output.append(_JSON_ESCAPES.get(ch, ch))
                    self._escape = False
                continue
            if ch == "\\":
                self._escape = True
                continue
            if ch == '"':
                self._state = "done"
                continue
            output.append(ch)

        return "".join(output)
