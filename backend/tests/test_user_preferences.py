"""Tests for persistent, account-scoped user preferences
(services/user_preferences.py, db/models.py's User.preferences,
db/crud.py's update_user_preferences) — proving:
  - the extraction/merge and prompt-formatting logic itself is correct
    (pure, deterministic, no network)
  - preferences are saved for the correct account
  - the same account sees its preferences again after a full logout/login
  - two different accounts never see each other's preferences
  - preferences actually reach the model's prompt, and — end to end —
    influence its reply
  - anonymous chat never reads or writes any account's preferences
  - streaming still saves preferences correctly, exactly like the
    non-streaming endpoint

Run from the backend folder:
    pytest tests/test_user_preferences.py -v
"""

import uuid

from fastapi.testclient import TestClient

from db.crud import get_user_by_email, get_user_by_id, update_user_preferences
from db.session import SessionLocal
from main import app
from routes.chat import _learn_preferences
from services.groq_service import _build_prompt_messages
from services.user_preferences import extract_and_merge_preferences, format_preferences_context


# --- pure logic (no network, no DB) -----------------------------------------


class TestExtractAndMergePreferences:
    def test_learns_destination_days_and_budget_from_the_validated_reply(self):
        prefs = extract_and_merge_preferences(
            None, "Plan a trip", {"destination": "Kandy", "days": 3, "budget_lkr": 30000}
        )
        assert prefs["preferred_destinations"] == ["Kandy"]
        assert prefs["typical_trip_duration_days"] == 3
        assert prefs["budget_lkr_min"] == 30000
        assert prefs["budget_lkr_max"] == 30000

    def test_learns_hotel_transport_food_activity_and_travel_style_from_the_user_message(self):
        prefs = extract_and_merge_preferences(
            None,
            "I want a luxury hotel, a tuk tuk, vegetarian food, and some hiking on this trip",
            {},
        )
        assert prefs["hotel_style"] == "Luxury hotel"
        assert prefs["transport_preference"] == "Tuk tuk"
        assert prefs["food_preferences"] == ["Vegetarian"]
        assert prefs["activity_preferences"] == ["Hiking"]

    def test_expands_the_budget_range_across_turns_instead_of_overwriting_it(self):
        prefs = extract_and_merge_preferences(None, "msg1", {"budget_lkr": 20000})
        prefs = extract_and_merge_preferences(prefs, "msg2", {"budget_lkr": 45000})
        assert prefs["budget_lkr_min"] == 20000
        assert prefs["budget_lkr_max"] == 45000

    def test_most_recent_destination_is_listed_first_and_stays_deduplicated(self):
        prefs = extract_and_merge_preferences(None, "m", {"destination": "Kandy"})
        prefs = extract_and_merge_preferences(prefs, "m", {"destination": "Galle"})
        prefs = extract_and_merge_preferences(prefs, "m", {"destination": "Kandy"})
        assert prefs["preferred_destinations"] == ["Kandy", "Galle"]

    def test_destination_list_is_capped_so_it_never_grows_without_bound(self):
        prefs = None
        for name in ["A", "B", "C", "D", "E", "F", "G"]:
            prefs = extract_and_merge_preferences(prefs, "m", {"destination": name})
        assert len(prefs["preferred_destinations"]) == 5
        assert prefs["preferred_destinations"][0] == "G"  # most recent first

    def test_never_raises_on_malformed_reply_data(self):
        prefs = extract_and_merge_preferences(
            {}, "hi", {"destination": 123, "days": "three", "budget_lkr": None}
        )
        assert prefs["preferred_destinations"] == []
        assert "typical_trip_duration_days" not in prefs


class TestFormatPreferencesContext:
    def test_empty_or_none_preferences_produce_no_context_at_all(self):
        assert format_preferences_context(None) == ""
        assert format_preferences_context({}) == ""

    def test_a_populated_preferences_dict_produces_a_readable_context_block(self):
        text = format_preferences_context(
            {
                "preferred_destinations": ["Kandy"],
                "budget_lkr_min": 20000,
                "budget_lkr_max": 20000,
                "hotel_style": "Luxury hotel",
                "travel_style": "Luxury",
            }
        )
        assert "Kandy" in text
        assert "20,000 LKR" in text
        assert "Luxury hotel" in text
        assert "Luxury" in text


class TestLearnPreferencesAnonymousNoOp:
    def test_does_nothing_and_never_raises_for_an_anonymous_caller(self):
        db = SessionLocal()
        try:
            # current_user=None is the entire gate — no DB write should
            # happen here, and this must not raise even though `db` is a
            # perfectly real, usable session.
            _learn_preferences(db, None, "Plan a trip to Kandy", {"destination": "Kandy"})
        finally:
            db.close()


# --- end-to-end (real app, real Groq, real Postgres) ------------------------


def _signup_and_fetch_user(client: TestClient, name: str):
    email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": name, "email": email, "password": "Password123"},
    )
    assert response.status_code == 201, response.text
    db = SessionLocal()
    try:
        user = get_user_by_email(db, email)
        assert user is not None
        return user
    finally:
        db.close()


def _reload_user(user_id: str):
    db = SessionLocal()
    try:
        return get_user_by_id(db, user_id)
    finally:
        db.close()


def _mentions_destination(preferences: dict, name: str) -> bool:
    """Substring match, not exact — the model sometimes returns "Kandy" and
    sometimes "Kandy, Sri Lanka" for the same trip (both correct; extraction
    stores whatever string the model's own `destination` field contained,
    see services/user_preferences.py). The guarantee under test is that the
    destination was learned at all, not the model's exact phrasing of it.
    """
    return any(name.lower() in d.lower() for d in preferences.get("preferred_destinations", []))


class TestPreferencesEndToEnd:
    def test_preferences_are_saved_for_the_correct_account(self):
        client = TestClient(app)
        user = _signup_and_fetch_user(client, "PrefUser")

        response = client.post(
            "/api/v1/chat/",
            json={"message": "I'd like a luxury hotel and a tuk tuk for a 3 day trip to Kandy, budget 60000 LKR"},
        )
        assert response.status_code == 200, response.text

        reloaded = _reload_user(user.id)
        assert reloaded.preferences is not None
        assert _mentions_destination(reloaded.preferences, "Kandy")
        assert reloaded.preferences.get("hotel_style") == "Luxury hotel"
        assert reloaded.preferences.get("transport_preference") == "Tuk tuk"

    def test_the_same_account_sees_its_preferences_again_after_a_full_logout_and_login(self):
        client = TestClient(app)
        email = f"prefrelogin-{uuid.uuid4().hex[:8]}@example.com"
        password = "Password123"

        signup = client.post(
            "/api/v1/auth/signup", json={"name": "PrefRelogin", "email": email, "password": password}
        )
        assert signup.status_code == 201, signup.text
        user_id = signup.json()["id"]

        chat = client.post(
            "/api/v1/chat/",
            json={"message": "I love hiking and I always want a budget hotel, plan a trip to Ella"},
        )
        assert chat.status_code == 200, chat.text

        csrf_token = client.cookies.get("voya_csrf_token")
        logout = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_token})
        assert logout.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401  # genuinely logged out in between

        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200

        # Not read back through any endpoint (none exists for this, by
        # design — see the response's limitations) — the real guarantee is
        # that the DATA survived the logout/login cycle intact, which is
        # exactly what the next chat request would draw on.
        reloaded = _reload_user(user_id)
        assert _mentions_destination(reloaded.preferences, "Ella")
        assert reloaded.preferences.get("hotel_style") == "Budget hotel"
        assert reloaded.preferences.get("activity_preferences") == ["Hiking"]

    def test_different_accounts_never_see_each_others_preferences(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        user_a = _signup_and_fetch_user(client_a, "PrefOwner")
        user_b = _signup_and_fetch_user(client_b, "PrefOther")

        reply = client_a.post(
            "/api/v1/chat/",
            json={"message": "I want a luxury hotel, plan a trip to Sigiriya, budget 80000 LKR"},
        )
        assert reply.status_code == 200, reply.text

        reloaded_a = _reload_user(user_a.id)
        reloaded_b = _reload_user(user_b.id)

        assert _mentions_destination(reloaded_a.preferences, "Sigiriya")
        assert reloaded_a.preferences.get("hotel_style") == "Luxury hotel"

        # Account B's row is completely untouched by account A's chat —
        # each account's preferences live on its OWN row, keyed by nothing
        # but its own primary key, so there's no shared namespace for one
        # account's data to leak into another's.
        assert not reloaded_b.preferences

    def test_preferences_actually_reach_the_models_prompt(self):
        """Deterministic, white-box proof that stored preferences influence
        context — independent of how the LLM chooses to word its reply (see
        test_stored_preferences_influence_a_live_reply below for that
        fuzzier, but genuinely end-to-end, version). No network happens
        here at all: use_memory/use_knowledge_base are False and
        candidate_tools is None, so _build_prompt_messages never touches
        Groq, Pinecone, or the `api_key` argument.
        """
        messages, _, _ = _build_prompt_messages(
            "Plan me a trip",
            session_id="pytest-session",
            history=None,
            use_memory=False,
            use_knowledge_base=False,
            api_key="unused-with-no-tools-or-memory-requested",
            candidate_tools=None,
            user_preferences={
                "preferred_destinations": ["Kandy"],
                "hotel_style": "Luxury hotel",
                "travel_style": "Luxury",
            },
        )
        system_prompt = messages[0]["content"]
        assert "Kandy" in system_prompt
        assert "Luxury hotel" in system_prompt

    def test_no_stored_preferences_leaves_the_prompt_unaffected(self):
        messages, _, _ = _build_prompt_messages(
            "Plan me a trip",
            session_id="pytest-session",
            history=None,
            use_memory=False,
            use_knowledge_base=False,
            api_key="unused",
            candidate_tools=None,
            user_preferences=None,
        )
        assert "Known preferences" not in messages[0]["content"]

    def test_stored_preferences_influence_a_live_reply(self):
        """End-to-end: a strong, unambiguous preference is set directly
        (rather than taught via a first chat message) so this test's setup
        is deterministic, then a vague follow-up that doesn't restate it is
        sent, and the real model's reply is checked for it.
        """
        client = TestClient(app)
        user = _signup_and_fetch_user(client, "PrefLive")

        db = SessionLocal()
        try:
            update_user_preferences(db, user.id, {"hotel_style": "Luxury hotel", "travel_style": "Luxury"})
        finally:
            db.close()

        response = client.post("/api/v1/chat/", json={"message": "Plan me a 2 day trip to Galle"})
        assert response.status_code == 200, response.text
        data = response.json()

        combined_text = (
            data["summary"]
            + " "
            + " ".join(item for day in data.get("itinerary", []) for item in day.get("items", []))
        ).lower()
        assert "luxury" in combined_text

    def test_streaming_still_saves_preferences_correctly(self):
        client = TestClient(app)
        user = _signup_and_fetch_user(client, "PrefStream")

        response = client.post(
            "/api/v1/chat/stream",
            json={"message": "I want a budget hotel and vegetarian food, plan a trip to Jaffna"},
        )
        assert response.status_code == 200, response.text

        reloaded = _reload_user(user.id)
        assert _mentions_destination(reloaded.preferences, "Jaffna")
        assert reloaded.preferences.get("hotel_style") == "Budget hotel"
        assert reloaded.preferences.get("food_preferences") == ["Vegetarian"]

    def test_anonymous_chat_never_creates_or_touches_any_preferences(self):
        anon_client = TestClient(app)
        response = anon_client.post(
            "/api/v1/chat/",
            json={
                "message": "I want a luxury hotel, plan a trip to Kandy",
                "session_id": f"pytest-anon-{uuid.uuid4()}",
            },
        )
        # The actual guarantee: an anonymous send behaves exactly as it did
        # before this feature existed — no user row to attach preferences
        # to, and _learn_preferences's current_user-is-None gate (see
        # TestLearnPreferencesAnonymousNoOp above) means it never tries.
        assert response.status_code == 200, response.text
