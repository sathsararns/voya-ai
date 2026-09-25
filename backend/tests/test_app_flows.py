"""Automated tests for Voya AI's most important backend flows: conversation
creation, follow-up continuity, and knowledge-base RAG retrieval/citations.

These are integration tests, not unit tests with mocks — consistent with how
the rest of this project is verified (see backend/scripts/eval_kb.py). They
exercise the real FastAPI app (via TestClient — no server needs to be
running separately), the real Postgres database, the real Pinecone index,
and the real Groq API. A working .env (DATABASE_URL, PINECONE_API_KEY,
PINECONE_INDEX_HOST, GROQ_API_KEY) is required to run them, same as running
the app itself.

Test-generated conversations use a "pytest-" session_id prefix so they're
easy to recognize (and, if ever needed, clean up) in the dev database and
in Pinecone's per-session memory namespaces.

Run from the backend folder:
    pytest
    pytest tests/test_app_flows.py -v
    pytest -k knowledge_base
"""

import uuid

from fastapi.testclient import TestClient

from main import app
from services.kb_rag import search_knowledge_base

client = TestClient(app)


def _new_session_id() -> str:
    return f"pytest-{uuid.uuid4()}"


def _send(message: str, session_id: str, conversation_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/chat/",
        json={"message": message, "session_id": session_id, "conversation_id": conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestConversationCreationAndContinuity:
    """Flow 4: sending a first message creates a real conversation.
    Flow 5: a follow-up message stays in that same conversation.
    """

    def test_first_message_creates_a_new_conversation(self):
        session_id = _new_session_id()

        reply = _send("Plan a 2 day trip to Galle", session_id)

        assert reply["conversation_id"]

        conversations = client.get(
            "/api/v1/chat/conversations", params={"session_id": session_id}
        ).json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["conversation_id"] == reply["conversation_id"]

    def test_followup_message_stays_in_the_same_conversation(self):
        session_id = _new_session_id()

        first = _send("Plan a 2 day trip to Galle", session_id)
        conversation_id = first["conversation_id"]

        second = _send("Make day 2 more relaxed", session_id, conversation_id=conversation_id)

        # Same conversation, not a second one.
        assert second["conversation_id"] == conversation_id

        conversations = client.get(
            "/api/v1/chat/conversations", params={"session_id": session_id}
        ).json()["conversations"]
        assert len(conversations) == 1

        history = client.get(f"/api/v1/chat/conversations/{conversation_id}/messages").json()
        assert len(history["messages"]) == 2


class TestKnowledgeBaseRAG:
    """Flow 6: KB retrieval returns relevant chunks for a factual question.
    Flow 7: KB citations (kb_sources) surface on the chat response.
    """

    def test_kb_retrieval_returns_relevant_chunks_for_a_factual_question(self):
        hits = search_knowledge_base("What currency is used in Sri Lanka?", top_k=4)

        assert len(hits) > 0
        combined_text = " ".join(h.get("fields", {}).get("text", "") for h in hits).lower()
        assert "rupee" in combined_text or "lkr" in combined_text

    def test_kb_citations_appear_on_the_chat_response_when_kb_is_used(self):
        session_id = _new_session_id()

        reply = _send("What are some tourist attractions in Kandy?", session_id)

        assert "kb_sources" in reply
        assert len(reply["kb_sources"]) > 0

        source = reply["kb_sources"][0]
        assert source["document_name"]
        assert source["title"]
        assert isinstance(source["snippet"], str) and source["snippet"]
