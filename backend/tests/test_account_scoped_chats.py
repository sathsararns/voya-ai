"""Integration tests proving Recent Chats and conversation access are
strictly account-scoped — the exact guarantee reported broken: a chat
created by one authenticated account must never be visible to, or
accessible by, a different account.

Uses two (or three) independent TestClient instances per test, each with
its own cookie jar — this mirrors two real accounts acting concurrently in
the same browser (the reported scenario), since TestClient's cookie jar is
exactly what a real browser tab's cookie store is standing in for.

These exercise the real FastAPI app end to end (routes -> db/crud.py ->
Postgres), same as tests/test_app_flows.py, not mocks.

Run from the backend folder:
    pytest tests/test_account_scoped_chats.py -v
"""

import uuid

from fastapi.testclient import TestClient

from main import app


def _signup(client: TestClient, name: str) -> dict:
    """Signup also signs the caller in (see routes/auth.py) — this is the
    only setup step each test needs per account.
    """
    email = f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": name, "email": email, "password": "Password123"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _send_chat(client: TestClient, message: str, session_id: str, conversation_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/chat/",
        json={"message": message, "session_id": session_id, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _list_conversations(client: TestClient, session_id: str) -> list:
    response = client.get("/api/v1/chat/conversations", params={"session_id": session_id})
    assert response.status_code == 200, response.text
    return response.json()["conversations"]


class TestRecentChatsUpdateAfterSend:
    """Flow reported broken: chat sends succeed, but the conversation
    doesn't show up in (or update within) Recent Chats. These prove the
    backend side of that pipeline directly — no session_id at all in the
    listing calls, matching exactly what the frontend now sends.
    """

    def test_sending_a_first_message_makes_the_conversation_appear_in_recent_chats(self):
        client = TestClient(app)
        _signup(client, "Tom")

        assert client.get("/api/v1/chat/conversations").json()["conversations"] == []

        reply = client.post("/api/v1/chat/", json={"message": "Plan a trip to Sigiriya"})
        assert reply.status_code == 200, reply.text
        conversation_id = reply.json()["conversation_id"]

        conversations = client.get("/api/v1/chat/conversations").json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["conversation_id"] == conversation_id
        assert "Sigiriya" in conversations[0]["title"]

    def test_a_followup_message_keeps_the_conversation_visible_and_bumps_its_updated_at(self):
        client = TestClient(app)
        _signup(client, "Tom")

        first = client.post("/api/v1/chat/", json={"message": "Plan a trip to Sigiriya"})
        conversation_id = first.json()["conversation_id"]
        before = client.get("/api/v1/chat/conversations").json()["conversations"][0]

        second = client.post(
            "/api/v1/chat/", json={"message": "Make it shorter", "conversation_id": conversation_id}
        )
        assert second.status_code == 200, second.text
        assert second.json()["conversation_id"] == conversation_id

        after_list = client.get("/api/v1/chat/conversations").json()["conversations"]
        assert len(after_list) == 1  # still one conversation, not a second one
        after = after_list[0]
        assert after["conversation_id"] == conversation_id
        assert after["updated_at"] > before["updated_at"]

    def test_sending_with_a_stale_previous_accounts_conversation_id_starts_a_fresh_one_for_this_account(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        _signup(client_a, "Tom")
        _signup(client_b, "Sathsara")

        toms_reply = client_a.post("/api/v1/chat/", json={"message": "Tom's trip"})
        toms_conversation_id = toms_reply.json()["conversation_id"]

        # Sathsara's client somehow ends up with Tom's conversation_id (e.g.
        # stale client state) and sends a message against it.
        sathsaras_reply = client_b.post(
            "/api/v1/chat/", json={"message": "Sathsara's message", "conversation_id": toms_conversation_id}
        )
        assert sathsaras_reply.status_code == 200
        assert sathsaras_reply.json()["conversation_id"] != toms_conversation_id

        # It shows up in Sathsara's Recent Chats, under her own new conversation id.
        sathsara_conversations = client_b.get("/api/v1/chat/conversations").json()["conversations"]
        assert len(sathsara_conversations) == 1
        assert sathsara_conversations[0]["conversation_id"] == sathsaras_reply.json()["conversation_id"]

        # Tom's own Recent Chats is unaffected — still just his one.
        tom_conversations = client_a.get("/api/v1/chat/conversations").json()["conversations"]
        assert len(tom_conversations) == 1
        assert tom_conversations[0]["conversation_id"] == toms_conversation_id


class TestAccountScopedRecentChats:
    """Every test below reuses the SAME session_id across both accounts —
    that's deliberate: session_id is the value that used to (incorrectly)
    scope Recent Chats, and is exactly what a real browser's localStorage
    keeps identical across logging out of one account and into another.
    Proving isolation holds even with a shared session_id is what proves
    user_id, not session_id, is actually doing the scoping.
    """

    def test_account_a_never_sees_account_bs_chat_in_recent_chats(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        shared_session_id = f"pytest-shared-{uuid.uuid4()}"

        _signup(client_a, "Tom")
        _signup(client_b, "Sathsara")

        _send_chat(client_a, "Tom's trip to Nuwara Eliya", shared_session_id)
        _send_chat(client_b, "Sathsara's trip to Jaffna", shared_session_id)

        a_conversations = _list_conversations(client_a, shared_session_id)
        b_conversations = _list_conversations(client_b, shared_session_id)

        assert len(a_conversations) == 1
        assert "Tom" in a_conversations[0]["title"]
        assert len(b_conversations) == 1
        assert "Sathsara" in b_conversations[0]["title"]

        a_ids = {c["conversation_id"] for c in a_conversations}
        b_ids = {c["conversation_id"] for c in b_conversations}
        assert a_ids.isdisjoint(b_ids)

    def test_cannot_read_another_accounts_conversation_by_id(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        anon_client = TestClient(app)
        shared_session_id = f"pytest-shared-{uuid.uuid4()}"

        _signup(client_a, "Tom")
        _signup(client_b, "Sathsara")

        reply = _send_chat(client_a, "Tom's private itinerary", shared_session_id)
        conversation_id = reply["conversation_id"]

        own_read = client_a.get(f"/api/v1/chat/conversations/{conversation_id}/messages")
        assert own_read.status_code == 200
        assert own_read.json()["messages"][0]["user_message"] == "Tom's private itinerary"

        other_account_read = client_b.get(f"/api/v1/chat/conversations/{conversation_id}/messages")
        assert other_account_read.status_code == 404

        anonymous_read = anon_client.get(f"/api/v1/chat/conversations/{conversation_id}/messages")
        assert anonymous_read.status_code == 404

    def test_cannot_continue_another_accounts_conversation(self):
        client_a = TestClient(app)
        client_b = TestClient(app)
        shared_session_id = f"pytest-shared-{uuid.uuid4()}"

        _signup(client_a, "Tom")
        _signup(client_b, "Sathsara")

        reply = _send_chat(client_a, "Tom's first message", shared_session_id)
        conversation_id = reply["conversation_id"]

        # Account B tries to piggyback on Account A's conversation_id.
        hijack_attempt = _send_chat(
            client_b, "Sathsara trying to hijack this thread", shared_session_id, conversation_id
        )

        # A brand new conversation was created for B instead of appending
        # to A's — the id the caller asked for is never reused across
        # accounts.
        assert hijack_attempt["conversation_id"] != conversation_id

        # A's conversation list is unaffected: still just the original.
        a_conversations = _list_conversations(client_a, shared_session_id)
        assert len(a_conversations) == 1
        assert a_conversations[0]["conversation_id"] == conversation_id

        # And B's message never became part of A's history.
        a_messages = client_a.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()["messages"]
        assert all("hijack" not in m["user_message"] for m in a_messages)

    def test_logout_then_different_account_login_shows_no_leak(self):
        # One shared cookie jar for both accounts — this is the literal
        # "same browser, switch accounts" scenario reported.
        client = TestClient(app)
        shared_session_id = f"pytest-shared-{uuid.uuid4()}"

        _signup(client, "Tom")
        _send_chat(client, "Tom's chat before logout", shared_session_id)
        assert len(_list_conversations(client, shared_session_id)) == 1

        csrf_token = client.cookies.get("voya_csrf_token")
        logout_response = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_token})
        assert logout_response.status_code == 200

        # A different account logs in, same cookie jar (same browser tab).
        _signup(client, "Sathsara")

        # Sathsara's list, queried with the EXACT same session_id Tom used,
        # must be empty — not Tom's chat, not merged, nothing.
        sathsara_conversations = _list_conversations(client, shared_session_id)
        assert sathsara_conversations == []

    def test_same_email_relogin_sees_the_same_chats_again(self):
        """The other half of account-scoping: not just 'a different email
        never leaks in', but 'the SAME email, after a full logout and a
        fresh login (a brand new session cookie, a brand new JWT), still
        sees exactly what it created before' — because the account
        (user_id) itself never changed, only the session did.
        """
        client = TestClient(app)
        email = f"tom-{uuid.uuid4().hex[:8]}@example.com"

        signup = client.post(
            "/api/v1/auth/signup", json={"name": "Tom", "email": email, "password": "Password123"}
        )
        assert signup.status_code == 201, signup.text

        reply = client.post("/api/v1/chat/", json={"message": "Tom's trip to Kandy"})
        conversation_id = reply.json()["conversation_id"]

        csrf_token = client.cookies.get("voya_csrf_token")
        logout_response = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_token})
        assert logout_response.status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401  # genuinely logged out in between

        login_response = client.post("/api/v1/auth/login", json={"email": email, "password": "Password123"})
        assert login_response.status_code == 200

        conversations = client.get("/api/v1/chat/conversations").json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["conversation_id"] == conversation_id

    def test_anonymous_and_authenticated_conversations_never_mix(self):
        client = TestClient(app)
        shared_session_id = f"pytest-shared-{uuid.uuid4()}"

        # Anonymous chat first (no login at all).
        _send_chat(client, "Anonymous message before any login", shared_session_id)
        assert len(_list_conversations(client, shared_session_id)) == 1

        # Now log in, same session_id, same cookie jar.
        _signup(client, "Tom")

        # Tom's list must NOT include the pre-login anonymous conversation.
        assert _list_conversations(client, shared_session_id) == []

    def test_authenticated_listing_works_with_no_session_id_sent_at_all(self):
        """The frontend no longer sends session_id for Recent Chats once
        logged in (see useAppStore.ts's apiListConversations) — this proves
        the backend never needed it for an authenticated caller in the
        first place: user_id (from the session cookie) is sufficient on
        its own, with the query param omitted entirely rather than merely
        empty.
        """
        client_a = TestClient(app)
        client_b = TestClient(app)

        _signup(client_a, "Tom")
        _signup(client_b, "Sathsara")

        # Sent with a session_id here only because /chat/ still uses it for
        # unrelated Pinecone memory continuity (see routes/chat.py) — this
        # is not what's being tested below.
        _send_chat(client_a, "Tom's trip, no session_id will be used to list it", str(uuid.uuid4()))
        _send_chat(client_b, "Sathsara's trip, no session_id will be used to list it", str(uuid.uuid4()))

        # The actual assertion: list WITHOUT the session_id query param.
        a_response = client_a.get("/api/v1/chat/conversations")
        b_response = client_b.get("/api/v1/chat/conversations")
        assert a_response.status_code == 200, a_response.text
        assert b_response.status_code == 200, b_response.text

        a_conversations = a_response.json()["conversations"]
        b_conversations = b_response.json()["conversations"]

        assert len(a_conversations) == 1
        assert "Tom" in a_conversations[0]["title"]
        assert len(b_conversations) == 1
        assert "Sathsara" in b_conversations[0]["title"]

    def test_anonymous_listing_without_session_id_returns_an_empty_list_not_an_error(self):
        """A caller with neither a session cookie nor a session_id can't be
        identified — but that's not a malformed request (nothing the
        caller sent was actually invalid), so it degrades to "nothing to
        show yet" rather than a hard 400. This also covers the real-world
        edge case of a request landing in the brief window before a fresh
        session cookie is fully committed.
        """
        anon_client = TestClient(app)
        response = anon_client.get("/api/v1/chat/conversations")
        assert response.status_code == 200
        assert response.json()["conversations"] == []

    def test_anonymous_listing_with_a_session_id_still_works_normally(self):
        """The session_id-scoped anonymous flow itself is unchanged — only
        the "neither identity available" case stopped being a 400.
        """
        anon_client = TestClient(app)
        session_id = f"pytest-anon-{uuid.uuid4()}"
        _send_chat(anon_client, "Anonymous chat", session_id)

        response = anon_client.get("/api/v1/chat/conversations", params={"session_id": session_id})
        assert response.status_code == 200
        assert len(response.json()["conversations"]) == 1
