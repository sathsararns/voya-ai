"""Tests for the manual preferences CRUD API
(GET/PUT/DELETE /api/v1/auth/preferences — routes/auth.py,
schemas/preferences.py), added so a user can view/edit/clear what
services/user_preferences.py's automatic, chat-driven extraction has
learned about them.

See tests/test_user_preferences.py for the automatic extraction/injection
side (chat teaching the assistant things, and those things reaching the
model's prompt) — this file is purely about the manual CRUD surface:
loading, editing/saving, clearing, and that one account's preferences are
never visible to or affected by another's.

Run from the backend folder:
    pytest tests/test_preferences_api.py -v
"""

import uuid

from fastapi.testclient import TestClient

from main import app

FULL_PREFERENCES_PAYLOAD = {
    "preferred_destinations": ["Kandy", "Galle"],
    "budget_lkr_min": 20000,
    "budget_lkr_max": 60000,
    "typical_trip_duration_days": 5,
    "hotel_style": "Luxury hotel",
    "transport_preference": "Tuk tuk",
    "food_preferences": ["Vegetarian"],
    "activity_preferences": ["Hiking"],
    "travel_style": "Luxury",
}

EMPTY_PREFERENCES = {
    "preferred_destinations": [],
    "budget_lkr_min": None,
    "budget_lkr_max": None,
    "typical_trip_duration_days": None,
    "hotel_style": None,
    "transport_preference": None,
    "food_preferences": [],
    "activity_preferences": [],
    "travel_style": None,
}


def _signup(client: TestClient, name: str) -> dict:
    email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": name, "email": email, "password": "Password123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("voya_csrf_token")
    assert token, "expected a CSRF cookie to already be set from signup"
    return token


class TestGetPreferences:
    def test_a_brand_new_account_has_empty_preferences(self):
        client = TestClient(app)
        _signup(client, "PrefApiUser")

        response = client.get("/api/v1/auth/preferences")
        assert response.status_code == 200, response.text
        assert response.json() == EMPTY_PREFERENCES

    def test_requires_authentication(self):
        anon_client = TestClient(app)
        response = anon_client.get("/api/v1/auth/preferences")
        assert response.status_code == 401


class TestUpdatePreferences:
    def test_saves_and_returns_the_full_edited_preferences(self):
        client = TestClient(app)
        _signup(client, "PrefApiEdit")

        response = client.put(
            "/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": _csrf(client)}
        )
        assert response.status_code == 200, response.text
        assert response.json() == FULL_PREFERENCES_PAYLOAD

    def test_a_subsequent_get_reflects_the_saved_edit(self):
        client = TestClient(app)
        _signup(client, "PrefApiReload")

        put_response = client.put(
            "/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": _csrf(client)}
        )
        assert put_response.status_code == 200, put_response.text

        get_response = client.get("/api/v1/auth/preferences")
        assert get_response.status_code == 200
        assert get_response.json() == FULL_PREFERENCES_PAYLOAD

    def test_a_second_edit_fully_replaces_the_first_not_merges_with_it(self):
        client = TestClient(app)
        _signup(client, "PrefApiReplace")
        csrf = _csrf(client)

        client.put("/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": csrf})

        second_payload = {
            **FULL_PREFERENCES_PAYLOAD,
            "hotel_style": "Budget hotel",
            "preferred_destinations": ["Ella"],
        }
        response = client.put("/api/v1/auth/preferences", json=second_payload, headers={"x-csrf-token": csrf})
        assert response.status_code == 200, response.text
        assert response.json()["hotel_style"] == "Budget hotel"
        assert response.json()["preferred_destinations"] == ["Ella"]

    def test_reversed_budget_range_is_corrected_not_rejected(self):
        client = TestClient(app)
        _signup(client, "PrefApiSwap")

        payload = {**FULL_PREFERENCES_PAYLOAD, "budget_lkr_min": 60000, "budget_lkr_max": 20000}
        response = client.put("/api/v1/auth/preferences", json=payload, headers={"x-csrf-token": _csrf(client)})
        assert response.status_code == 200, response.text
        assert response.json()["budget_lkr_min"] == 20000
        assert response.json()["budget_lkr_max"] == 60000

    def test_extra_list_items_beyond_the_cap_are_trimmed_not_rejected(self):
        client = TestClient(app)
        _signup(client, "PrefApiCap")

        payload = {**FULL_PREFERENCES_PAYLOAD, "preferred_destinations": ["A", "B", "C", "D", "E", "F", "G"]}
        response = client.put("/api/v1/auth/preferences", json=payload, headers={"x-csrf-token": _csrf(client)})
        assert response.status_code == 200, response.text
        assert response.json()["preferred_destinations"] == ["A", "B", "C", "D", "E"]

    def test_blank_and_whitespace_only_list_entries_are_dropped(self):
        client = TestClient(app)
        _signup(client, "PrefApiBlank")

        payload = {**FULL_PREFERENCES_PAYLOAD, "food_preferences": ["Vegetarian", "   ", ""]}
        response = client.put("/api/v1/auth/preferences", json=payload, headers={"x-csrf-token": _csrf(client)})
        assert response.status_code == 200, response.text
        assert response.json()["food_preferences"] == ["Vegetarian"]

    def test_rejects_the_edit_without_a_csrf_token(self):
        client = TestClient(app)
        _signup(client, "PrefApiNoCsrf")

        response = client.put("/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD)
        assert response.status_code == 403

    def test_requires_authentication(self):
        anon_client = TestClient(app)
        response = anon_client.put("/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD)
        assert response.status_code == 401


class TestClearPreferences:
    def test_clears_all_saved_preferences(self):
        client = TestClient(app)
        _signup(client, "PrefApiClear")
        csrf = _csrf(client)
        client.put("/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": csrf})

        response = client.delete("/api/v1/auth/preferences", headers={"x-csrf-token": csrf})
        assert response.status_code == 200, response.text
        assert response.json() == EMPTY_PREFERENCES

        # A subsequent GET reflects the clear — not stale cached data from
        # before the DELETE.
        get_response = client.get("/api/v1/auth/preferences")
        assert get_response.json() == EMPTY_PREFERENCES

    def test_rejects_clearing_without_a_csrf_token(self):
        client = TestClient(app)
        _signup(client, "PrefApiClearNoCsrf")

        response = client.delete("/api/v1/auth/preferences")
        assert response.status_code == 403

    def test_requires_authentication(self):
        anon_client = TestClient(app)
        response = anon_client.delete("/api/v1/auth/preferences")
        assert response.status_code == 401


class TestPreferencesAccountIsolation:
    def test_one_account_cannot_see_or_affect_another_accounts_preferences(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        _signup(client_a, "PrefApiOwner")
        _signup(client_b, "PrefApiOther")

        client_a.put(
            "/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": _csrf(client_a)}
        )

        # B's own GET is completely untouched by A's edit — each account's
        # preferences live on its own row, keyed only by its own primary
        # key (see db/models.py's User.preferences), so there's no shared
        # namespace for A's data to leak into B's view.
        b_response = client_b.get("/api/v1/auth/preferences")
        assert b_response.json() == EMPTY_PREFERENCES

        # B editing THEIR OWN preferences never touches A's.
        client_b.put(
            "/api/v1/auth/preferences",
            json={**FULL_PREFERENCES_PAYLOAD, "hotel_style": "Budget hotel"},
            headers={"x-csrf-token": _csrf(client_b)},
        )
        a_response = client_a.get("/api/v1/auth/preferences")
        assert a_response.json()["hotel_style"] == "Luxury hotel"  # unaffected by B's edit

    def test_clearing_one_accounts_preferences_never_affects_another_accounts(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        _signup(client_a, "PrefApiClearOwner")
        _signup(client_b, "PrefApiClearOther")

        client_a.put(
            "/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": _csrf(client_a)}
        )
        client_b.put(
            "/api/v1/auth/preferences", json=FULL_PREFERENCES_PAYLOAD, headers={"x-csrf-token": _csrf(client_b)}
        )

        client_a.delete("/api/v1/auth/preferences", headers={"x-csrf-token": _csrf(client_a)})

        assert client_a.get("/api/v1/auth/preferences").json() == EMPTY_PREFERENCES
        # B's own preferences are still fully intact.
        assert client_b.get("/api/v1/auth/preferences").json() == FULL_PREFERENCES_PAYLOAD


class TestManualEditsAndChatDrivenLearningShareOneStore:
    """Not a new guarantee on its own — confirms the manual CRUD API added
    here and the automatic extraction path (see test_user_preferences.py)
    read/write the exact same underlying column (db/models.py's
    User.preferences), so a manual edit is immediately what the assistant's
    next reply would be personalized with, with no separate sync step.
    """

    def test_a_manual_edit_is_immediately_visible_through_get_preferences(self):
        client = TestClient(app)
        _signup(client, "PrefApiSharedStore")

        client.put(
            "/api/v1/auth/preferences",
            json={**FULL_PREFERENCES_PAYLOAD, "hotel_style": "Boutique hotel"},
            headers={"x-csrf-token": _csrf(client)},
        )

        response = client.get("/api/v1/auth/preferences")
        assert response.json()["hotel_style"] == "Boutique hotel"
