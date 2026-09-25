"""Integration tests for core account-identity guarantees: one email maps
to exactly one account, forever, and only that email+password pair can log
into it.

These are the foundation every Recent-Chats ownership guarantee in
test_account_scoped_chats.py depends on — user_id-based conversation
scoping is only as strong as "one email = one stable user_id, and nobody
else can obtain a session for it". If email uniqueness or login credential
matching were ever broken, every downstream ownership check would inherit
the same break.

Run from the backend folder:
    pytest tests/test_auth.py -v
"""

import uuid

from fastapi.testclient import TestClient

from main import app


def _unique_email(name: str) -> str:
    return f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"


class TestEmailAccountIdentity:
    def test_signup_creates_a_usable_account(self):
        client = TestClient(app)
        email = _unique_email("tom")

        response = client.post(
            "/api/v1/auth/signup", json={"name": "Tom", "email": email, "password": "Password123"}
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email"] == email
        assert "id" in body
        # Signup signs the caller in immediately (see routes/auth.py) — no
        # separate login step required.
        assert client.get("/api/v1/auth/me").status_code == 200

    def test_duplicate_email_signup_is_rejected(self):
        client = TestClient(app)
        email = _unique_email("tom")

        first = client.post(
            "/api/v1/auth/signup", json={"name": "Tom", "email": email, "password": "Password123"}
        )
        assert first.status_code == 201, first.text

        # Same email, different name/password — still the same account slot.
        second = client.post(
            "/api/v1/auth/signup",
            json={"name": "Tom Again", "email": email, "password": "DifferentPass456"},
        )
        assert second.status_code == 400

    def test_duplicate_email_signup_is_rejected_case_insensitively(self):
        """Emails are stored lowercased (see db/crud.py's create_user /
        get_user_by_email) — a signup with different casing of an existing
        email is the same account, not a new one.
        """
        client = TestClient(app)
        base = uuid.uuid4().hex[:8]
        email_lower = f"tom-{base}@example.com"
        email_mixed_case = f"Tom-{base}@Example.com"

        first = client.post(
            "/api/v1/auth/signup", json={"name": "Tom", "email": email_lower, "password": "Password123"}
        )
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/v1/auth/signup",
            json={"name": "Tom", "email": email_mixed_case, "password": "Password123"},
        )
        assert second.status_code == 400

    def test_login_requires_the_correct_password(self):
        client = TestClient(app)
        email = _unique_email("tom")
        client.post("/api/v1/auth/signup", json={"name": "Tom", "email": email, "password": "Password123"})

        # A fresh client — no leftover session from signup's auto-login —
        # tries the wrong password.
        response = TestClient(app).post(
            "/api/v1/auth/login", json={"email": email, "password": "WrongPassword999"}
        )
        assert response.status_code == 401

    def test_login_requires_a_registered_email(self):
        response = TestClient(app).post(
            "/api/v1/auth/login", json={"email": _unique_email("nobody"), "password": "Password123"}
        )
        assert response.status_code == 401

    def test_login_with_the_correct_email_and_password_succeeds(self):
        client = TestClient(app)
        email = _unique_email("tom")
        client.post("/api/v1/auth/signup", json={"name": "Tom", "email": email, "password": "Password123"})

        response = TestClient(app).post("/api/v1/auth/login", json={"email": email, "password": "Password123"})

        assert response.status_code == 200
        assert response.json()["email"] == email
