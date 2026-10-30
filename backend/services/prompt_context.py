"""Reduce repeated UI data without changing the history used by validators."""

import json

from services.trip_state import parse_plan


def compact_history(history):
    """Keep user instructions, options and the newest complete itinerary.

    Older plans retain their facts and summaries. Citations, repeated budget
    tables and presentation-only fields need not be sent back to the model.
    The original records remain intact for validation and chat persistence.
    """
    latest = next((i for i in range(len(history or []) - 1, -1, -1)
                   if parse_plan(history[i]) is not None), None)
    result = []
    keys = ("destination", "days", "budget_lkr", "budget_cap_lkr", "budget_source",
            "summary", "follow_up_question", "planning_context")
    for i, turn in enumerate(history or []):
        if turn.get("role") != "assistant":
            result.append(dict(turn))
            continue
        try:
            data = json.loads(turn.get("content", ""))
        except (ValueError, TypeError):
            result.append(dict(turn))
            continue
        if not isinstance(data, dict):
            result.append(dict(turn))
            continue
        compact = {key: data[key] for key in keys if key in data}
        if i == latest:
            compact["itinerary"] = data["itinerary"]
        elif data.get("itinerary"):
            compact["previous_day_titles"] = [day.get("title", "") for day in data["itinerary"]]
        else:
            compact["itinerary"] = []
        result.append({"role": "assistant", "content": json.dumps(
            compact, ensure_ascii=False, separators=(",", ":"))})
    return result
