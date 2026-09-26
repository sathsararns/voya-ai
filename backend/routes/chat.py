import json
from typing import Optional
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatHistoryItem,
    ChatHistoryResponse,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationListResponse,
)
from services.groq_service import get_groq_reply, stream_groq_reply
from services.history_manager import build_context, to_groq_messages
from services.context_router import decide_context_strategy
from db.models import User
from db.session import SessionLocal
from db.crud import (
    save_chat,
    get_conversation,
    get_conversation_messages,
    count_conversation_messages,
    create_conversation,
    get_or_create_conversation,
    list_conversations,
    touch_conversation,
)
from pinecone_memory import save_memory
from memory_utils import build_memory_text
from routes.auth import get_current_user_optional

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# Used only if a client calls an endpoint without a session_id at all.
FALLBACK_SESSION_ID = "anonymous-session"

CONVERSATION_TITLE_MAX_LENGTH = 60


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _deserialize_reply(raw_reply: str) -> dict:
    """Parse a stored assistant_reply JSON string back into a dict.

    Mirrors the fallback shape used in groq_service.get_groq_reply so old or
    malformed rows still produce a valid ChatResponse instead of a 500.
    """
    try:
        return json.loads(raw_reply)
    except (json.JSONDecodeError, TypeError):
        return {
            "destination": None,
            "days": None,
            "budget_lkr": None,
            "summary": raw_reply or "",
            "itinerary": [],
            "follow_up_question": None,
            "kb_sources": [],
        }


def _default_title(user_message: str) -> str:
    text = user_message.strip()
    if len(text) > CONVERSATION_TITLE_MAX_LENGTH:
        return f"{text[:CONVERSATION_TITLE_MAX_LENGTH]}…"
    return text


@router.post("/", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    session_id = request.session_id or FALLBACK_SESSION_ID
    user_id = current_user.id if current_user else None
    conversation = get_or_create_conversation(db, session_id, request.conversation_id, user_id=user_id)
    conversation_id = conversation.conversation_id

    # Agentic RAG decision layer: figure out which context sources this
    # message actually needs BEFORE touching the DB/Pinecone for them, so a
    # greeting or a one-word command skips retrieval entirely instead of
    # paying for it and throwing the result away.
    message_count = count_conversation_messages(db, conversation_id)
    decision = decide_context_strategy(request.message, message_count)
    print(
        f"[context_router] reason={decision.route_reason} "
        f"history={decision.use_conversation_history} "
        f"memory={decision.use_pinecone_memory} "
        f"summarized={decision.use_summarized_context} "
        f"knowledge_base={decision.use_knowledge_base}"
    )

    history = []
    if decision.use_conversation_history:
        context = build_context(
            db, conversation_id, prefer_summary=decision.use_summarized_context
        )
        history = to_groq_messages(context)

    reply_data = get_groq_reply(
        request.message,
        session_id,
        history=history,
        use_memory=decision.use_pinecone_memory,
        use_knowledge_base=decision.use_knowledge_base,
    )

    save_chat(db, request.message, reply_data, session_id, conversation_id)
    touch_conversation(db, conversation_id, title=_default_title(request.message))

    memory_text = build_memory_text(request.message, reply_data)

    if memory_text:
        save_memory(
            session_id=session_id,
            memory_id=str(uuid4()),
            text=memory_text,
            metadata={
                "memory_type": "preference",
                "session_id": session_id,
                "conversation_id": conversation_id,
                "user_message": request.message,
                "assistant_reply": json.dumps(reply_data),
            },
        )

    reply_data["conversation_id"] = conversation_id
    return reply_data


# Generic wording reused from main.py's RedisError handler, for the same
# reason: don't leak which internal step failed to the client, just that
# something did.
SERVICE_UNAVAILABLE_DETAIL = "A required service is temporarily unavailable. Please try again shortly."


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/stream")
def chat_stream(
    request: ChatRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Same request/response contract as POST / (same auth, same ownership,
    same final saved message), delivered as Server-Sent Events instead of a
    single JSON body — so the frontend can render the assistant's reply
    token-by-token as the model generates it (see json_utils's
    SummaryStreamExtractor and services/groq_service.stream_groq_reply)
    instead of waiting for the whole thing.

    Event stream contract:
      event: token   data: {"text": "..."}   — zero or more, visible text as
                                                it's generated
      event: done    data: <ChatResponse>    — exactly one, terminal (on a
                                                completed reply); the
                                                authoritative final reply,
                                                identical in shape to what
                                                POST / returns
      event: error   data: {"detail": "..."} — only for a failure severe
                                                enough that no reply could be
                                                produced or saved at all
                                                (Groq itself failing still
                                                surfaces as a normal `done`
                                                with an apology summary —
                                                see stream_groq_reply)

    If the client disconnects (a real network drop, or the frontend's Stop
    button aborting its fetch — see frontend/src/hooks/useAppStore.ts's
    stopGenerating) partway through, NEITHER of those terminal events is
    sent — there's no one left to receive them — and, more importantly,
    nothing about that turn is saved: no save_chat, no touch_conversation,
    no Pinecone memory write. See event_stream()'s own comments for exactly
    how that's detected and enforced.

    Conversation ownership is resolved up front, exactly like POST / — the
    stream never starts until get_or_create_conversation has already
    decided (and get_current_user_optional has already enforced) whose
    conversation this is.
    """
    session_id = request.session_id or FALLBACK_SESSION_ID
    user_id = current_user.id if current_user else None
    conversation = get_or_create_conversation(db, session_id, request.conversation_id, user_id=user_id)
    conversation_id = conversation.conversation_id

    message_count = count_conversation_messages(db, conversation_id)
    decision = decide_context_strategy(request.message, message_count)
    print(
        f"[context_router] reason={decision.route_reason} "
        f"history={decision.use_conversation_history} "
        f"memory={decision.use_pinecone_memory} "
        f"summarized={decision.use_summarized_context} "
        f"knowledge_base={decision.use_knowledge_base}"
    )

    history = []
    if decision.use_conversation_history:
        context = build_context(
            db, conversation_id, prefer_summary=decision.use_summarized_context
        )
        history = to_groq_messages(context)

    async def event_stream():
        # An async generator (not a plain sync one, like the rest of this
        # module's route bodies) specifically so this can `await
        # http_request.is_disconnected()` between chunks — Starlette runs it
        # directly on the event loop rather than in a worker thread, which is
        # also why every blocking call inside it below (pulling from
        # stream_groq_reply, and the DB/Pinecone writes at the end) is
        # explicitly wrapped in run_in_threadpool: without that, a slow Groq
        # response or a slow DB write would stall every other request this
        # server is handling concurrently, not just this one.
        reply_data: Optional[dict] = None
        aborted = False

        reply_generator = stream_groq_reply(
            request.message,
            session_id,
            history=history,
            use_memory=decision.use_pinecone_memory,
            use_knowledge_base=decision.use_knowledge_base,
        )

        try:
            while True:
                # Checked before every single pull, not just once up front —
                # this is what stops Groq generation as soon as possible
                # after OUR OWN check happens to notice a disconnect.
                # is_disconnected() is non-blocking (see Starlette's
                # implementation: it only ever reports what's already
                # queued, never waits), so this adds no latency of its own.
                #
                # This is deliberately a second line of defense, not the
                # only one: for a StreamingResponse specifically, Starlette
                # itself already races a concurrent "listen for disconnect"
                # task against this generator (see StreamingResponse.__call__
                # — the non-websocket ASGI spec version uvicorn reports takes
                # that code path) and cancels this whole generator the
                # moment ITS listener sees the disconnect, independent of
                # whether we ever call is_disconnected() ourselves. That
                # mechanism is more prompt than polling once-per-chunk could
                # ever be, since it's continuously awaiting — but it can, in
                # principle, land at any await point, including partway
                # through the save below. That's exactly why the save block
                # further down is shielded rather than relying on this loop
                # alone to make cancellation safe.
                if await http_request.is_disconnected():
                    aborted = True
                    break

                try:
                    kind, payload = await run_in_threadpool(next, reply_generator)
                except StopIteration:
                    break
                except Exception:
                    # stream_groq_reply itself never raises past its own
                    # try/except (see its docstring) — this only guards
                    # against something failing in this pull/SSE-framing
                    # layer, so a broken connection surfaces as one clean
                    # error event instead of a half-written stream.
                    yield _sse_event("error", {"detail": SERVICE_UNAVAILABLE_DETAIL})
                    return

                if kind == "delta":
                    yield _sse_event("token", {"text": payload})
                elif kind == "done":
                    reply_data = payload
                    break  # nothing left to pull; avoid one pointless extra iteration
                # "tick": no visible text in this particular chunk — nothing
                # to send, but pulling it (and looping back to the
                # is_disconnected() check above) is the whole point; see
                # stream_groq_reply's own docstring for why these exist.
        finally:
            # Closing the generator here is what actually propagates a
            # disconnect (or any other early exit from this loop) into
            # stream_groq_reply as a GeneratorExit — see its own docstring —
            # which stops it from reading any further chunks from Groq and
            # releases the underlying HTTP connection. Safe to call
            # unconditionally: closing an already-finished generator is a
            # documented no-op. Also runs if Starlette's own disconnect
            # listener cancels this generator while the loop above is still
            # running — GeneratorExit takes priority the same way it would
            # for our own early `break`.
            reply_generator.close()

        if aborted:
            # The client is already gone: no one to send a terminal event
            # to, and — the actual point of this whole function — nothing
            # earned a save. A generation that never finished is not a
            # "reply", partial or otherwise; it never reaches save_chat.
            return

        if reply_data is None:
            yield _sse_event("error", {"detail": SERVICE_UNAVAILABLE_DETAIL})
            return

        # Generation genuinely finished (reply_data is set) — this is no
        # longer "an aborted generation" even if the client's connection
        # drops at this exact moment (a real disconnect that lands here
        # raced with, rather than preceded, the reply completing). The save
        # itself is shielded from that outer cancellation on purpose:
        # Starlette's own disconnect-driven cancellation (see the loop
        # above's comment) can land at any `await` — including partway
        # through save_chat/touch_conversation/save_memory — and unshielded,
        # that produces exactly the inconsistent state this whole feature
        # exists to prevent, just from the opposite direction: a message
        # saved via save_chat with touch_conversation never having run to
        # title it, because the cancellation happened to land between the
        # two calls. Once we've decided there's a complete reply to
        # persist, persisting it is treated as atomic — all three calls
        # happen, or (only on a genuine internal failure, caught below)
        # none of their effects are surfaced as a successful save.
        save_failed = False
        with anyio.CancelScope(shield=True):
            try:
                await run_in_threadpool(save_chat, db, request.message, reply_data, session_id, conversation_id)
                await run_in_threadpool(
                    touch_conversation, db, conversation_id, title=_default_title(request.message)
                )

                memory_text = build_memory_text(request.message, reply_data)
                if memory_text:
                    await run_in_threadpool(
                        save_memory,
                        session_id=session_id,
                        memory_id=str(uuid4()),
                        text=memory_text,
                        metadata={
                            "memory_type": "preference",
                            "session_id": session_id,
                            "conversation_id": conversation_id,
                            "user_message": request.message,
                            "assistant_reply": json.dumps(reply_data),
                        },
                    )
            except Exception:
                save_failed = True

        if save_failed:
            yield _sse_event("error", {"detail": SERVICE_UNAVAILABLE_DETAIL})
            return

        reply_data["conversation_id"] = conversation_id
        yield _sse_event("done", reply_data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Stops an intermediary reverse proxy (e.g. nginx) from
            # buffering the whole response before forwarding it, which
            # would silently turn this back into a non-streamed reply.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations", response_model=ConversationResponse)
def create_new_conversation(
    request: ConversationCreateRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    session_id = request.session_id or FALLBACK_SESSION_ID
    user_id = current_user.id if current_user else None
    conversation = create_conversation(db, session_id, request.title, user_id=user_id)
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        session_id=conversation.session_id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/conversations", response_model=ConversationListResponse)
def get_conversations(
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # A logged-in caller is scoped by account (session_id, if sent, is
    # ignored — see list_conversations) so switching accounts in the same
    # browser only ever shows that account's own chats, and the frontend no
    # longer sends session_id for this call at all once authenticated (see
    # useAppStore.ts's apiListConversations). An anonymous caller still
    # needs a session_id to scope by, same as before auth existed — but
    # with neither a session cookie nor a session_id, there is simply no
    # identity to list conversations for. That's not a malformed request
    # (every field the client sent, or omitted, was valid on its own), so
    # it isn't a 400 — it's "nothing to show yet", the same empty result a
    # real caller with zero conversations would get. This also means a
    # brief window where the session cookie hasn't landed yet (e.g. right
    # after login, before it's committed) degrades to an empty list rather
    # than a hard error.
    if current_user is None and not session_id:
        return ConversationListResponse(conversations=[])

    conversations = list_conversations(
        db, session_id=session_id, user_id=current_user.id if current_user else None, limit=limit
    )
    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                conversation_id=c.conversation_id,
                session_id=c.session_id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in conversations
        ]
    )


@router.get("/conversations/{conversation_id}/messages", response_model=ChatHistoryResponse)
def get_conversation_history(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    # Same ownership rule as get_or_create_conversation: a conversation's
    # owner (None for anonymous) must match the caller exactly. Without
    # this, any caller who learned a conversation_id — by guessing a UUID
    # is infeasible, but e.g. a shared link, browser history, or a bug
    # elsewhere — could read another account's messages by id alone.
    conversation = get_conversation(db, conversation_id)
    caller_id = current_user.id if current_user else None
    if conversation is None or conversation.user_id != caller_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    records = get_conversation_messages(db, conversation_id, limit=limit)

    messages = [
        ChatHistoryItem(
            id=record.id,
            user_message=record.user_message,
            assistant_reply=_deserialize_reply(record.assistant_reply),
            created_at=record.created_at,
        )
        for record in records
    ]

    return ChatHistoryResponse(conversation_id=conversation_id, messages=messages)
