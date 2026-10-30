from services.context_router import is_information_only_request
from services.budget import extract_budget_cap
from services.preference_policy import learning_disabled


def build_memory_text(user_message: str, reply_data: dict) -> str:
    if learning_disabled(user_message) or reply_data.get("_skip_preference_learning"):
        return ""
    if is_information_only_request(user_message):
        return ""
    parts = []

    destination = reply_data.get("destination")
    days = reply_data.get("days")
    budget = extract_budget_cap(user_message)

    if budget:
        parts.append(f"User previously stated a total trip budget cap of LKR {budget}.")
    if days:
        parts.append(f"User often plans {days}-day trips.")
    if destination:
        parts.append(f"User asked about {destination}.")

    lower_msg = user_message.lower()

    if "beach" in lower_msg:
        parts.append("User is interested in beach destinations.")
    if "kandy" in lower_msg:
        parts.append("User is interested in Kandy trips.")
    if "family" in lower_msg:
        parts.append("User may want family-friendly trips.")
    if "luxury" in lower_msg:
        parts.append("User may prefer luxury travel.")
    if any(phrase in lower_msg for phrase in ("budget-friendly", "budget travel", "low budget", "budget trip")):
        parts.append("User prefers budget-friendly travel.")

    return " ".join(parts).strip()
