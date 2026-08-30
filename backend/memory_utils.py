def build_memory_text(user_message: str, reply_data: dict) -> str:
    parts = []

    destination = reply_data.get("destination")
    days = reply_data.get("days")
    budget = reply_data.get("budget_lkr")

    if budget:
        parts.append("User likes budget travel.")
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
    if "budget" in lower_msg:
        parts.append("User prefers budget-friendly travel.")

    return " ".join(parts).strip()