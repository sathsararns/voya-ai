import json
import os
import re
from typing import Dict, Iterator, List, Optional, Tuple

from groq import BadRequestError, Groq
from dotenv import load_dotenv

from json_utils import normalize_chat_response
from pinecone_memory import format_memory_context
from services.kb_rag import KnowledgeBaseUnavailable, build_kb_sources, format_kb_context, search_knowledge_base
from services.retrieval_policy import build_search_query
from services.response_validator import validate_ai_response
from services.local_context import local_tool_context
from services.prompt_context import compact_history
from services.user_preferences import format_preferences_context
from services.context_router import is_options_request, is_preference_statement
from services.answer_policy import information_fallback, non_trip_answer, normalize_answer_shape, planning_preflight_reply
from services.budget import BUDGET_PRESENTATION_INSTRUCTION, attach_budget_details, extract_budget_cap, requests_saved_budget, resolve_budget_cap, saved_budget_range
from services.fee_policy import FEE_INSTRUCTION, FEE_NOTICE
from services.venue_facts import venue_location_context
from services.itinerary_edits import addition_prompt_context
from services.planning_context import attach_planning_context, planning_prompt_context
from services.provider_retry import failure_details, log_failure, open_completion_stream, request_completion
from services.transport_policy import TRANSPORT_INSTRUCTION
from services.trip_state import scoped_trip_history, last_plan
from services.preference_policy import learning_disabled, learning_requested

load_dotenv()

GROQ_MODEL = "openai/gpt-oss-120b"
MAX_COMPLETION_TOKENS = 4096

# Strict output is used for non-streamed generation and recovery. The
# streaming call keeps JSON mode because Groq's strict mode cannot stream.
CHAT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "travel_reply",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "destination": {"type": ["string", "null"]},
                "days": {"type": ["integer", "null"]},
                "budget_lkr": {"type": ["integer", "null"]},
                "summary": {"type": "string"},
                "itinerary": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "day": {"type": "integer"},
                            "title": {"type": "string"},
                            "items": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["day", "title", "items"],
                    },
                },
                "follow_up_question": {"type": ["string", "null"]},
            },
            "required": ["destination", "days", "budget_lkr", "summary", "itinerary", "follow_up_question"],
        },
    },
}

FINAL_ANSWER_INSTRUCTION = (
    "Tool execution is complete for this turn. Use the supplied results "
    "and conversation to write the final JSON answer. Do not call tools, "
    "emit function calls, or request additional tool execution. "
    "Each itinerary activity must be a separate plain string in items; "
    "never put a serialized JSON list or array delimiters inside a string."
    " Treat any stated total budget as a hard maximum. Category allocations "
    "are estimates, not verified quotes. Do not invent free entry or discounts."
)

GENERATION_FAILURE_MESSAGE = "I couldn't finish that response. Please try your request again."


class IncompleteGenerationError(ValueError):
    """Generation hit its output limit before a complete answer was ready."""


def _can_retry_generation(error: Exception) -> bool:
    if isinstance(error, (IncompleteGenerationError, json.JSONDecodeError)):
        return True
    if not isinstance(error, BadRequestError):
        return False
    body = error.body if isinstance(error.body, dict) else {}
    details = body.get("error", body)
    code = details.get("code") if isinstance(details, dict) else None
    return code in {"tool_use_failed", "json_validate_failed"} or (
        "tool choice is none" in str(error).lower()
    )

CONVERSATION_CONTEXT_RULES = (
    "Use recent conversation turns to resolve short replies and references. "
    "Resolve numbered or ordinal selections such as 'I choose 2', 'option 3', "
    "'2', or 'the second one' to the matching option from the most recent "
    "travel options you offered. "
    "An option number is not a number of trip days. Preserve the selected "
    "option's destinations and constraints. The user's current selection "
    "takes priority over saved preferences and older itineraries. "
    "If the referenced options are missing or the selection is ambiguous, "
    "ask a clarifying question instead of inventing an unrelated trip.\n"
)

SYSTEM_PROMPT = (
    "You are Voya AI, a travel assistant.\n"
    + CONVERSATION_CONTEXT_RULES
    + FEE_INSTRUCTION
    + BUDGET_PRESENTATION_INSTRUCTION
    + TRANSPORT_INSTRUCTION
    + "Respond with ONLY one valid JSON object. No markdown, no code fences, "
    "no explanation, and no text before or after the JSON.\n"
    "Match this exact schema and field names:\n"
    "{\n"
    '  "destination": string or null,\n'
    '  "days": number or null,\n'
    '  "budget_lkr": number or null,\n'
    '  "summary": string,\n'
    '  "itinerary": [\n'
    "    {\n"
    '      "day": number,\n'
    '      "title": string,\n'
    '      "items": string[]\n'
    "    }\n"
    "  ],\n"
    '  "follow_up_question": string or null\n'
    "}\n"
    'Each itinerary day must use the field name "items" for its list of '
    'activities — never "activities" or any other name.\n'
)


def _call_groq(client: Groq, messages: List[Dict[str, str]], temperature: float) -> str:
    final_messages = messages + [{"role": "system", "content": FINAL_ANSWER_INSTRUCTION}]
    for attempt in range(2):
        try:
            response = request_completion(client,
                model=GROQ_MODEL,
                messages=final_messages,
                reasoning_effort="low",
                max_completion_tokens=MAX_COMPLETION_TOKENS * (attempt + 1),
                temperature=temperature,
                response_format=CHAT_RESPONSE_FORMAT,
                tool_choice="none",
            )
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise IncompleteGenerationError("Output token limit reached")
            content = choice.message.content or ""
            json.loads(content)
            return content
        except (BadRequestError, IncompleteGenerationError, json.JSONDecodeError) as error:
            if attempt or not _can_retry_generation(error):
                raise
    raise IncompleteGenerationError("No complete response")


def _fallback_reply(summary: str, error_code: str = "generation_failed", retry_after_seconds=None) -> dict:
    """The safe, schema-valid stand-in used whenever a reply couldn't
    actually be produced (missing API key, Groq/network failure, etc.) —
    shared by get_groq_reply and stream_groq_reply so both surface exactly
    the same shape on failure instead of one of them ever raising past
    routes/chat.py.
    """
    return {
        "destination": None,
        "days": None,
        "budget_lkr": None,
        "summary": summary,
        "itinerary": [],
        "follow_up_question": None,
        "kb_sources": [],
        "tools_used": [],
        "error_code": error_code,
        "retry_after_seconds": retry_after_seconds,
    }


def _provider_failure_reply(stage: str, error: Exception) -> dict:
    log_failure(stage, error)
    code, message, delay = failure_details(error)
    return _fallback_reply(message, code, delay)


def _build_prompt_messages(
    user_message: str,
    session_id: Optional[str],
    history: Optional[List[Dict[str, str]]],
    use_memory: bool,
    use_knowledge_base: bool,
    api_key: str,
    candidate_tools: Optional[List[str]] = None,
    user_preferences: Optional[Dict[str, object]] = None,
    retrieval_state: Optional[dict] = None,
) -> Tuple[List[Dict[str, str]], List[dict], List[str]]:
    """Shared setup for both get_groq_reply and stream_groq_reply: the
    memory/KB/tool/preferences lookups, the system prompt, and the final
    message list handed to Groq. Kept in one place so streaming and
    non-streaming replies are always grounded in identical context.
    """
    history = scoped_trip_history(user_message, history)
    # use_memory/use_knowledge_base come from the context_router decision —
    # when it decides a source isn't relevant to this message, skip that
    # lookup entirely rather than querying and discarding it. Memory
    # (pinecone_memory.py) and the knowledge base (services/kb_rag.py) live
    # in the same Pinecone index but separate namespaces, so these two calls
    # never touch each other's data.
    memory_context = format_memory_context(session_id, user_message, top_k=3) if use_memory else ""

    # One KB search, reused for both the prompt text (kb_context) and the
    # UI source citations (kb_sources) — avoids a second Pinecone call and
    # keeps the reference list consistent with the evidence made available
    # to generation. Retrieval does not prove every generated claim.
    kb_hits = []
    kb_status = "not_requested"
    if use_knowledge_base and not is_preference_statement(user_message):
        try:
            kb_hits = search_knowledge_base(build_search_query(user_message, history), top_k=4)
            kb_status = "evidence_found" if kb_hits else "no_supporting_evidence"
        except KnowledgeBaseUnavailable:
            kb_status = "unavailable"
    kb_context = format_kb_context(kb_hits)
    kb_sources = build_kb_sources(kb_hits)
    if retrieval_state is not None:
        retrieval_state["status"] = kb_status

    # Budget arithmetic and climate lookups are deterministic. Reuse the
    # existing filtered KB evidence for places/itinerary planning instead of
    # asking a second model to select tools and repeating the same search.
    tool_context, tool_names_used = local_tool_context(
        user_message, candidate_tools, history,
    ) if not is_preference_statement(user_message) else ("", [])

    # Unlike memory/KB/tools, this costs no extra I/O to include — the
    # caller (routes/chat.py) already has `user_preferences` in hand from
    # the User row get_current_user_optional resolved for this request, so
    # there's no "is it worth a round trip" gate here the way there is for
    # the others. format_preferences_context returns "" for None/empty, so
    # an anonymous caller (user_preferences never passed at all) or a
    # first-time account (nothing learned yet) sees a prompt identical to
    # one built before this feature existed.
    preferences_context = format_preferences_context(user_preferences)

    system_prompt = SYSTEM_PROMPT
    # The coastal venue catalogue is useful only when those places occur
    # in this turn's request, active history or retrieved references.
    venue_text = " ".join([user_message, kb_context, *(t.get("content", "") for t in history or [])])
    if re.search(r"\b(?:galle|bentota|beruwala|kalawila|brief gardens?|maritime museum|groote kerk)\b", venue_text, re.I):
        system_prompt += venue_location_context()
    if non_trip_answer(user_message):
        system_prompt += (
            "\nThis is a factual question, attraction list or preference acknowledgement, "
            "not a request to build a trip. Answer entirely in summary (use bullets "
            "there if useful). Set itinerary to [], destination/days/budget_lkr "
            "to null. Do not invent day containers or entrance-fee notices.\n"
        )
    if use_knowledge_base:
        system_prompt += (
            f"\nKnowledge retrieval status: {kb_status}. "
            "Only the supplied references support sourced factual claims. "
            "If no supporting evidence was found, say the requested fact cannot "
            "be verified from the available sources. If unavailable, explain "
            "the knowledge lookup temporarily failed. Do not invent citations, "
            "fees, opening hours or train departures. You may still offer a "
            "clearly labelled tentative itinerary without guaranteeing live facts.\n"
        )
    if is_options_request(user_message):
        system_prompt += (
            "\nThe user is comparing travel options, not choosing a trip yet. "
            "Briefly describe each requested option as a numbered list in "
            "summary, set destination/days/budget_lkr to null and itinerary "
            "to [], and ask which option they prefer in follow_up_question. "
            "Do not ask them to reconfirm every option's days or budget.\n"
        )
    if preferences_context:
        system_prompt += f"\n{preferences_context}\n"
    system_prompt += planning_prompt_context(user_message, history)
    if memory_context:
        system_prompt += f"\n{memory_context}\n"
    if kb_context:
        system_prompt += (
            f"\n{kb_context}\n"
            "\nGround any factual claims above (visas, currency, safety, "
            "customs, weather, etc.) in those knowledge-base facts rather "
            "than guessing. Don't list them as itinerary days unless the "
            "user actually asked for an itinerary.\n"
        )
    if tool_context:
        system_prompt += f"\n{tool_context}\n"
    budget_cap_lkr = resolve_budget_cap(user_message, history)
    limits = saved_budget_range(user_preferences)
    if requests_saved_budget(user_message) and limits and extract_budget_cap(user_message) is None:
        upper = min(limits[1], budget_cap_lkr) if budget_cap_lkr is not None else limits[1]
        system_prompt += (
            f"\nThe user explicitly asks to use the confirmed saved budget range. "
            f"For an itinerary, return a numeric budget_lkr between {limits[0]} and {upper}. "
            "This is an estimated allocation, not verified prices. Do not omit the total "
            "or call an unsupported allowance a saved preference.\n"
        )
    if budget_cap_lkr is not None:
        system_prompt += (
            f"\nThe user's current TOTAL budget cap is LKR {budget_cap_lkr:,}. "
            "It is a hard maximum for this entire trip, not a per-day budget. "
            "Keep budget_lkr at or below it, preserve it on follow-up edits, "
            "and describe costs as planning estimates. Do not claim that "
            "unverified prices guarantee affordability.\n"
        )
    edit_context = addition_prompt_context(user_message, history)
    if edit_context:
        system_prompt += "\n" + edit_context

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(compact_history(history))
    messages.append({"role": "user", "content": user_message})

    return messages, kb_sources, tool_names_used


def _make_retry(client: Groq, messages: List[Dict[str, str]]):
    def retry_with_feedback(issues: List[str]) -> dict:
        # One stricter re-ask, telling the model exactly what was wrong
        # with its last answer instead of just asking again blindly.
        correction = (
            "Your previous reply had these problems:\n"
            + "\n".join(f"- {issue}" for issue in issues)
            + "\nReturn ONE corrected JSON object that fixes all of them, still "
            "matching the exact schema from the system prompt above. No markdown, "
            "no extra text."
        )
        retry_messages = messages + [{"role": "user", "content": correction}]
        retry_content = _call_groq(client, retry_messages, temperature=0.2)
        return normalize_chat_response(retry_content)

    return retry_with_feedback


def _prepare_reply(user_message: str, normalized: dict, retry_fn, history=None, user_preferences=None) -> dict:
    history = scoped_trip_history(user_message, history)
    normalized = normalize_answer_shape(user_message, normalized)
    def retry_with_shape(issues):
        return normalize_answer_shape(user_message, retry_fn(issues))
    validated = validate_ai_response(user_message, normalized, retry_with_shape, history=history, user_preferences=user_preferences)
    previous = last_plan(history)
    skip_learning = learning_disabled(user_message) or (
        bool(previous and previous.get("_skip_preference_learning")) and not learning_requested(user_message)
    )
    validated.pop("_skip_preference_learning", None)
    if skip_learning:
        validated["_skip_preference_learning"] = True
    if non_trip_answer(user_message):
        return normalize_answer_shape(user_message, validated)
    validated = attach_planning_context(validated, user_message, history)
    validated = attach_budget_details(validated, resolve_budget_cap(user_message, history), preferences=user_preferences)
    if validated.get("itinerary"):
        validated["fee_notice"] = FEE_NOTICE
    return validated


def _attach_retrieval_details(reply: dict, sources: list[dict]) -> None:
    if reply.get("error_code"):
        reply["kb_sources"] = []
        return
    reply["kb_sources"] = sources
    # Do not repeat a server notice copied from an older turn when the
    # current answer has no supporting timetable reference. Rebuild once
    # from the current retrieval, rather than trusting generated notices.
    reply["summary"] = re.sub(r"(?m)^Train reference: notice dated[^\n]*\n?", "", reply["summary"]).strip()
    # A dated timetable must never look like a current service guarantee,
    # even when the model omits the prompt's caveat. This is server-owned.
    dates = sorted({s["source_date"] for s in sources if s.get("route_origin") and s.get("source_date")})
    if dates:
        reply["summary"] = reply["summary"].rstrip() + (
            "\n\nTrain reference: notice dated " + ", ".join(dates)
            + " (partial reviewed transcription). Services may have changed; "
            "confirm current departures with Sri Lanka Railways before travel."
        )


def get_groq_reply(
    user_message: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_memory: bool = True,
    use_knowledge_base: bool = True,
    candidate_tools: Optional[List[str]] = None,
    user_preferences: Optional[Dict[str, object]] = None,
) -> dict:
    history = scoped_trip_history(user_message, history)
    preflight = planning_preflight_reply(user_message, history, user_preferences)
    if preflight is not None:
        return preflight
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    try:
        retrieval_state = {}
        messages, kb_sources, tool_names_used = _build_prompt_messages(
            user_message,
            session_id,
            history,
            use_memory,
            use_knowledge_base,
            api_key,
            candidate_tools,
            user_preferences,
            retrieval_state=retrieval_state,
        )

        direct = information_fallback(user_message, kb_sources, retrieval_state.get("status", ""))
        if direct is not None:
            direct.update(kb_sources=[], tools_used=tool_names_used)
            return direct

        client = Groq(api_key=api_key, timeout=45, max_retries=0)
        content = _call_groq(client, messages, temperature=0.4)
        normalized = normalize_chat_response(content)

        validated = _prepare_reply(user_message, normalized, _make_retry(client, messages), history, user_preferences)
        _attach_retrieval_details(validated, kb_sources)
        validated["tools_used"] = tool_names_used
        return validated

    except Exception as e:
        return _provider_failure_reply("reply", e)


def stream_groq_reply(
    user_message: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_memory: bool = True,
    use_knowledge_base: bool = True,
    candidate_tools: Optional[List[str]] = None,
    user_preferences: Optional[Dict[str, object]] = None,
) -> Iterator[Tuple[str, object]]:
    """Same reply pipeline as get_groq_reply (same prompt, same
    normalize/validate/retry, same kb_sources). Yields ("tick", None) for
    generation progress, then ("delta", validated_summary) and one terminal
    ("done", validated_dict) — never raises.

    Text is held until validation completes so an over-budget estimate or
    unsupported fee claim is never briefly displayed before a correction.
    The frontend still types the validated summary and itinerary gradually.
    The done payload remains the source of truth for persistence.

    The ("tick", None) events exist purely for cancellation, not display:
    routes/chat.py's chat_stream pulls one item from this generator at a
    time and checks for a client disconnect between each pull. If this
    generator only ever yielded on visible `summary` text, a disconnect
    happening after `summary` had already finished generating (typical for
    a long itinerary, where `summary` is a small fraction of the total
    output) would go completely undetected until the ENTIRE rest of the
    reply had already been generated in one uninterrupted pull — silently
    defeating the whole point of checking for a disconnect at all. Yielding
    a checkpoint for every raw chunk, not just the ones with visible text,
    is what keeps the gap between "client disconnects" and "generation
    actually stops" bounded by one Groq network chunk instead of by however
    much of the reply was left to generate.

    A retry triggered by validate_ai_response happens after the primary
    stream has already finished (it needs the complete first attempt to
    judge), and is not itself streamed — a rare path (most replies pass
    validation on the first try), and its own final text still reaches the
    caller via the terminal ("done", ...) event, same as everything else
    validate_ai_response can change.

    Cancellable: if the caller stops consuming this generator before it's
    finished (routes/chat.py's chat_stream does this — see its own
    docstring — when the client disconnects mid-stream), Python throws
    GeneratorExit in here at whatever `yield` this is currently suspended
    on. That isn't caught below (GeneratorExit is a BaseException, not an
    Exception, so the `except Exception` clause never sees it), so it
    propagates straight out — no further chunks get read from Groq, no
    "done" fallback gets yielded, nothing gets returned to a caller that
    already stopped listening. The `finally` block still runs first, though,
    which is what actually releases the underlying HTTP connection to Groq
    instead of leaving it dangling.

    Deterministic tools and a single filtered KB lookup prepare the prompt
    without a separate model tool-selection call. The usual successful turn
    uses one model completion; validation/recovery can still need another.
    """
    history = scoped_trip_history(user_message, history)
    preflight = planning_preflight_reply(user_message, history, user_preferences)
    if preflight is not None:
        yield ("delta", preflight["summary"])
        yield ("done", preflight)
        return
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        yield ("done", _fallback_reply("Groq error: GROQ_API_KEY is missing"))
        return

    completion_stream = None
    try:
        retrieval_state = {}
        messages, kb_sources, tool_names_used = _build_prompt_messages(
            user_message,
            session_id,
            history,
            use_memory,
            use_knowledge_base,
            api_key,
            candidate_tools,
            user_preferences,
            retrieval_state=retrieval_state,
        )

        direct = information_fallback(user_message, kb_sources, retrieval_state.get("status", ""))
        if direct is not None:
            direct.update(kb_sources=[], tools_used=tool_names_used)
            yield ("delta", direct["summary"])
            yield ("done", direct)
            return

        client = Groq(api_key=api_key, timeout=45, max_retries=0)
        raw_chunks: List[str] = []

        for event, payload in open_completion_stream(client,
            model=GROQ_MODEL,
            messages=messages + [{"role": "system", "content": FINAL_ANSWER_INSTRUCTION}],
            reasoning_effort="low",
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            temperature=0.4,
            response_format={"type": "json_object"},
            tool_choice="none",
            stream=True,
        ):
            if event == "tick":
                yield "tick", None
            else:
                completion_stream = payload
        finish_reason = None
        for chunk in completion_stream:
            if not chunk.choices:
                yield ("tick", None)
                continue
            choice = chunk.choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            delta = choice.delta.content
            if delta:
                raw_chunks.append(delta)

            # This particular raw chunk had nothing visible in it — no
            # delta at all, or one that landed outside the `summary` string
            # (destination/itinerary/budget/etc, which for a long itinerary
            # is most of the response by volume). Yield a lightweight
            # checkpoint anyway rather than silently looping straight to the
            # next chunk: every `yield` here is a point where the caller
            # (routes/chat.py's chat_stream) gets to check for a client
            # disconnect before this generator is resumed. Without this,
            # once the visible `summary` text finished early in the
            # response, the entire rest of the reply would generate as one
            # single uninterruptible pull from the caller's perspective —
            # exactly the gap that let an aborted generation keep running
            # (and get saved) in practice, since most replies' `summary` is
            # a small fraction of their total token count.
            yield ("tick", None)

        content = "".join(raw_chunks)
        if finish_reason == "length":
            raise IncompleteGenerationError("Output token limit reached")
        # Never repair a truncated stream into a seemingly successful plan.
        # Recover using a complete, strictly structured answer instead.
        json.loads(content)
        normalized = normalize_chat_response(content)

        validated = _prepare_reply(user_message, normalized, _make_retry(client, messages), history, user_preferences)
        _attach_retrieval_details(validated, kb_sources)
        validated["tools_used"] = tool_names_used
        yield ("delta", validated["summary"])
        yield ("done", validated)

    except Exception as e:
        if _can_retry_generation(e):
            try:
                if completion_stream is not None:
                    completion_stream.close()
                    completion_stream = None
                content = _call_groq(client, messages, temperature=0.2)
                normalized = normalize_chat_response(content)
                validated = _prepare_reply(user_message, normalized, _make_retry(client, messages), history, user_preferences)
                _attach_retrieval_details(validated, kb_sources)
                validated["tools_used"] = tool_names_used
                yield ("delta", validated["summary"])
                yield ("done", validated)
                return
            except Exception as retry_error:
                e = retry_error
        yield ("done", _provider_failure_reply("streamed_reply", e))
    finally:
        # Runs on every exit path: normal completion, the fallback above, AND
        # an early GeneratorExit from the caller closing us — release()/close()
        # is a documented no-op if the response was already read to
        # completion, so this is safe to always call rather than tracking
        # whether it's "necessary" in each branch.
        if completion_stream is not None:
            completion_stream.close()
