"""Request/response shapes for the user-preferences API (routes/auth.py's
/preferences endpoints). The stored shape itself (what keys exist, what they
mean) is owned by services/user_preferences.py — these models mirror it
exactly so the automatic, chat-driven extraction and the manual UI edit path
both read/write the same fields the same way.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from services.user_preferences import MAX_LIST_ITEMS

# Free-text fields (hotel_style, transport_preference, travel_style) and each
# individual list item are capped at this length — generous for a short
# label ("Luxury hotel", "Cultural sightseeing"), not for arbitrary prose.
# Purely a sanity bound against a malformed/abusive request; the UI's own
# inputs are plain text fields with nothing this would ever legitimately hit.
MAX_FIELD_LENGTH = 100


def _clean_list(values: List[str]) -> List[str]:
    """Strips, drops empties, and caps to MAX_LIST_ITEMS — the same shape
    guarantee services/user_preferences.py's own _push_distinct already
    gives the auto-extracted path, so a manual edit can't produce a bigger
    or messier list than chat-driven learning ever could.
    """
    cleaned = [v.strip()[:MAX_FIELD_LENGTH] for v in values if v and v.strip()]
    return cleaned[:MAX_LIST_ITEMS]


def _clean_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed[:MAX_FIELD_LENGTH] or None


class UserPreferences(BaseModel):
    """The full preferences shape — same fields
    services/user_preferences.py's extract_and_merge_preferences produces,
    returned as-is by GET /preferences and PUT /preferences."""

    preferred_destinations: List[str] = Field(default_factory=list)
    budget_lkr_min: Optional[int] = None
    budget_lkr_max: Optional[int] = None
    typical_trip_duration_days: Optional[int] = None
    hotel_style: Optional[str] = None
    transport_preference: Optional[str] = None
    food_preferences: List[str] = Field(default_factory=list)
    activity_preferences: List[str] = Field(default_factory=list)
    travel_style: Optional[str] = None

    @classmethod
    def from_stored(cls, preferences: Optional[dict]) -> "UserPreferences":
        """Builds this response model from whatever is on User.preferences —
        None (never saved anything yet) included, since every field here
        already has a safe default.
        """
        return cls(**(preferences or {}))


class UserPreferencesUpdate(BaseModel):
    """The editable form the Settings page submits — a full replace, not a
    partial patch: whatever the client sends becomes the new stored value
    for every field (see routes/auth.py's update_preferences), matching how
    the form always round-trips every field it loaded. Every field defaults
    to "empty" so a field the user cleared in the form is saved as cleared,
    not left at its old value.
    """

    preferred_destinations: List[str] = Field(default_factory=list)
    budget_lkr_min: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    budget_lkr_max: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    typical_trip_duration_days: Optional[int] = Field(default=None, ge=1, le=365)
    hotel_style: Optional[str] = None
    transport_preference: Optional[str] = None
    food_preferences: List[str] = Field(default_factory=list)
    activity_preferences: List[str] = Field(default_factory=list)
    travel_style: Optional[str] = None

    _clean_destinations = field_validator("preferred_destinations")(_clean_list)
    _clean_food = field_validator("food_preferences")(_clean_list)
    _clean_activities = field_validator("activity_preferences")(_clean_list)
    _clean_hotel_style = field_validator("hotel_style")(_clean_optional_str)
    _clean_transport = field_validator("transport_preference")(_clean_optional_str)
    _clean_travel_style = field_validator("travel_style")(_clean_optional_str)

    @model_validator(mode="after")
    def _swap_budget_if_reversed(self) -> "UserPreferencesUpdate":
        # A user-friendly correction rather than a rejection: if both are
        # given and reversed (max < min), swapping them is obviously what
        # was meant, and rejecting the whole save over it would just be
        # annoying for a mistake this unambiguous. A model-level validator
        # (not a per-field one) because this needs to read AND write both
        # fields together — a field validator can only return a new value
        # for the one field it's attached to.
        if (
            self.budget_lkr_min is not None
            and self.budget_lkr_max is not None
            and self.budget_lkr_max < self.budget_lkr_min
        ):
            self.budget_lkr_min, self.budget_lkr_max = self.budget_lkr_max, self.budget_lkr_min
        return self

    def to_stored_dict(self) -> dict:
        """The dict shape actually persisted (db/crud.py's
        update_user_preferences) — same field set as UserPreferences, so a
        save followed by a reload round-trips exactly.
        """
        return self.model_dump()
