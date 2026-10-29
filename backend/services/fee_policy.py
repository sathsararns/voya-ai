"""Reject unsupported fee guarantees instead of treating model prose as proof.

There is currently no current-price verification service. KB passages and
previous assistant replies are not verified admission or discount quotes.
"""

import re

FEE_NOTICE = "Entrance fees are unverified. Confirm current charges with each venue."
FEE_INSTRUCTION = (
    "No current admission-fee verification service is available. Do not claim "
    "that attractions, entry, tours or equipment are free, no-cost, discounted, "
    "or have a low/reduced entrance fee. Do not invent exact admission prices "
    "or free-entry days. Prior assistant replies are not evidence. Omit "
    "unverified prices and use 'entry fee unverified; confirm with the venue' "
    "when discussing admission. Free time and dietary phrases such as "
    "gluten-free are fine. Budget categories are planning allowances, not "
    "verified prices or a guarantee of what the trip will actually cost.\n"
)

_FREE_CLAIM = re.compile(
    r"\bfree\b(?!\s+(?:time|afternoon|morning|evening|day|hours?|to\b))"
    r"|\b(?:no[ -]cost|zero[ -]cost|complimentary)\b"
    r"|\b(?:at\s+)?no\s+(?:extra\s+)?charge\b", re.IGNORECASE,
)
_LOW_FEE = re.compile(
    r"\b(?:low|reduced|discounted|nominal|small|cheap)\s+(?:entrance\s+|entry\s+|admission\s+)?fees?\b",
    re.IGNORECASE,
)
_FEE_AMOUNT = re.compile(
    r"\b(?:entry|entrance|admission|ticket|fee)\b[^.!?\n]{0,45}\b(?:LKR|Rs\.?)\s*\d"
    r"|\b(?:LKR|Rs\.?)\s*\d[\d,]*[^.!?\n]{0,25}\b(?:entry|entrance|admission|ticket|fee)\b",
    re.IGNORECASE,
)


def validate_fee_claims(data: dict) -> list[str]:
    texts = [data.get("summary") or ""]
    for day in data.get("itinerary") or []:
        if isinstance(day, dict):
            texts.append(day.get("title") or "")
            texts.extend(item for item in (day.get("items") or []) if isinstance(item, str))
    for text in texts:
        text = re.sub(r"\b(?:gluten|dairy|sugar|nut|meat|alcohol|stress|hassle)[ -]free\b", "", text,
                      flags=re.IGNORECASE)
        text = re.sub(r"\b(?:not|isn't|aren't)\s+free\b", "", text, flags=re.IGNORECASE)
        if _FREE_CLAIM.search(text) or _LOW_FEE.search(text) or _FEE_AMOUNT.search(text):
            return [
                "Unverified fee claim: remove free/no-cost/discounted admission "
                "or exact entrance prices; mark admission fees as unverified "
                "and ask the user to confirm with the venue."
            ]
    return []
