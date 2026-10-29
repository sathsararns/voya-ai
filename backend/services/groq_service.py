import json
import os
from typing import Dict, Iterator, List, Optional, Tuple

from groq import BadRequestError, Groq
from dotenv import load_dotenv

from json_utils import normalize_chat_response
from pinecone_memory import format_memory_context
from services.kb_rag import build_kb_sources, format_kb_context, search_knowledge_base
from services.response_validator import validate_ai_response
from services.tools import TOOL_SCHEMAS, execute_tool_call
from services.user_preferences import format_preferences_context
from services.context_router import is_options_request
from services.budget import BUDGET_PRESENTATION_INSTRUCTION, attach_budget_details, resolve_budget_cap
from services.fee_policy import FEE_INSTRUCTION, FEE_NOTICE
from services.venue_facts import venue_location_context
from services.itinerary_edits import addition_prompt_context

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
    + "\n" + BUDGET_PRESENTATION_INSTRUCTION
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
    + venue_location_context()
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
            response = client.chat.completions.create(
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


def _fallback_reply(summary: str) -> dict:
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
        "follow_up_question": "Please try again.",
        "kb_sources": [],
        "tools_used": [],
    }


# System prompt for the separate, lightweight tool-decision call (see
# _run_tool_calling_phase) — deliberately NOT the main SYSTEM_PROMPT: that
# one demands a single strict JSON object and says nothing about tools,
# which is exactly what the FINAL answer call still needs (tool results are
# folded into ITS system prompt as plain context instead — see
# _build_prompt_messages below). Keeping the two prompts/calls separate means
# tool-calling and the existing strict-JSON-mode generation never have to
# coexist in the same Groq request, which sidesteps any question of whether
# a given provider supports both at once.
TOOL_DECISION_SYSTEM_PROMPT = (
    "You are a travel-planning assistant deciding whether any tools are "
    "needed to answer the user's message well. Call a tool only when it "
    "would genuinely improve the answer (e.g. the user asked about weather, "
    "budget, specific places, or wants a day-by-day itinerary). If no tool "
    "is needed, respond with no tool calls at all.\n"
    + CONVERSATION_CONTEXT_RULES
)

# Upper bound on how many tool calls one turn will actually execute, even if
# the model requests more — keeps a single turn's cost/latency bounded
# regardless of what the model asks for.
MAX_TOOL_CALLS_PER_TURN = 4


def _run_tool_calling_phase(
    client: Groq, user_message: str, candidate_tools: List[str],
    history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[str]]:
    """Offers the model only the tool(s) context_router.py flagged as
    plausibly relevant for this message (candidate_tools), and lets it
    decide — via Groq's own function-calling (tool_choice="auto") — whether
    to actually call any of them. Genuine model agency, just scoped to a
    short, relevant menu instead of always offering all four tools to every
    message (same reasoning context_router already applies to
    memory/history/KB: skip the round trip when it obviously isn't needed).

    Never raises: a failed tool-decision call, a tool the model asks for
    that doesn't exist, or a tool that errors internally all just mean no
    tool context gets added — exactly as if the model had chosen not to
    call anything. Returns (context_block, tool_names_used); context_block
    is "" when nothing was called, ready to be spliced into the main system
    prompt alongside memory_context/kb_context.
    """
    if not candidate_tools:
        return "", []

    schemas = [s for s in TOOL_SCHEMAS if s["function"]["name"] in candidate_tools]
    if not schemas:
        return "", []

    # Tools need the same recent choices as final generation; otherwise
    # a weather/budget request about "option 2" can use a guessed place.
    messages = [{"role": "system", "content": TOOL_DECISION_SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=schemas,
            tool_choice="auto",
            max_tokens=512,
            temperature=0.2,
        )
    except Exception as e:
        print(f"[tools] tool-decision call failed: {e}")
        return "", []

    tool_calls = getattr(response.choices[0].message, "tool_calls", None) or []
    if not tool_calls:
        return "", []

    tool_names_used: List[str] = []
    result_lines = [
        "Tool results (real data — ground your answer in these where "
        "relevant, but still respond using the exact JSON schema below):"
    ]
    for call in tool_calls[:MAX_TOOL_CALLS_PER_TURN]:
        name = call.function.name
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
        result = execute_tool_call(name, arguments)
        tool_names_used.append(name)
        result_lines.append(f"- {name}({json.dumps(arguments)}): {json.dumps(result)}")

    return "\n".join(result_lines), tool_names_used


def _build_prompt_messages(
    user_message: str,
    session_id: Optional[str],
    history: Optional[List[Dict[str, str]]],
    use_memory: bool,
    use_knowledge_base: bool,
    api_key: str,
    candidate_tools: Optional[List[str]] = None,
    user_preferences: Optional[Dict[str, object]] = None,
) -> Tuple[List[Dict[str, str]], List[dict], List[str]]:
    """Shared setup for both get_groq_reply and stream_groq_reply: the
    memory/KB/tool/preferences lookups, the system prompt, and the final
    message list handed to Groq. Kept in one place so streaming and
    non-streaming replies are always grounded in identical context.
    """
    # use_memory/use_knowledge_base come from the context_router decision —
    # when it decides a source isn't relevant to this message, skip that
    # lookup entirely rather than querying and discarding it. Memory
    # (pinecone_memory.py) and the knowledge base (services/kb_rag.py) live
    # in the same Pinecone index but separate namespaces, so these two calls
    # never touch each other's data.
    memory_context = format_memory_context(session_id, user_message, top_k=3) if use_memory else ""

    # One KB search, reused for both the prompt text (kb_context) and the
    # UI source citations (kb_sources) — avoids a second Pinecone call and
    # guarantees the citations shown to the user are exactly the chunks
    # that actually grounded the answer.
    kb_hits = search_knowledge_base(user_message, top_k=4) if use_knowledge_base else []
    kb_context = format_kb_context(kb_hits)
    kb_sources = build_kb_sources(kb_hits)

    # A separate, throwaway Groq client for the tool-decision round only —
    # cheap to construct (same pattern get_groq_reply/stream_groq_reply
    # already use for their own client), and keeping it separate from the
    # main generation client makes the two calls' independence explicit.
    tool_context = ""
    tool_names_used: List[str] = []
    if candidate_tools:
        tool_client = Groq(api_key=api_key)
        tool_context, tool_names_used = _run_tool_calling_phase(
            tool_client, user_message, candidate_tools, history=history
        )

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
        messages.extend(history)
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


def _prepare_reply(user_message: str, normalized: dict, retry_fn, history=None) -> dict:
    validated = validate_ai_response(user_message, normalized, retry_fn, history=history)
    validated = attach_budget_details(validated, resolve_budget_cap(user_message, history))
    if validated.get("itinerary"):
        validated["fee_notice"] = FEE_NOTICE
    return validated


def get_groq_reply(
    user_message: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_memory: bool = True,
    use_knowledge_base: bool = True,
    candidate_tools: Optional[List[str]] = None,
    user_preferences: Optional[Dict[str, object]] = None,
) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    try:
        messages, kb_sources, tool_names_used = _build_prompt_messages(
            user_message,
            session_id,
            history,
            use_memory,
            use_knowledge_base,
            api_key,
            candidate_tools,
            user_preferences,
        )

        client = Groq(api_key=api_key)
        content = _call_groq(client, messages, temperature=0.4)
        normalized = normalize_chat_response(content)

        validated = _prepare_reply(user_message, normalized, _make_retry(client, messages), history)
        validated["kb_sources"] = kb_sources
        validated["tools_used"] = tool_names_used
        return validated

    except Exception as e:
        print(f"[groq] reply failed: {type(e).__name__}")
        return _fallback_reply(GENERATION_FAILURE_MESSAGE)


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

    If `candidate_tools` is non-empty, a separate, non-streamed tool-decision
    call happens first (see _build_prompt_messages/_run_tool_calling_phase) —
    entirely before this function's own Groq stream even starts, so it adds
    a bounded amount of latency before the first token, but never changes
    the streaming contract itself: tool results just become more context
    folded into the same system prompt memory_context/kb_context already
    use, and any tools actually used surface in the terminal ("done", ...)
    payload's `tools_used` list, same as `kb_sources` already does.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        yield ("done", _fallback_reply("Groq error: GROQ_API_KEY is missing"))
        return

    completion_stream = None
    try:
        messages, kb_sources, tool_names_used = _build_prompt_messages(
            user_message,
            session_id,
            history,
            use_memory,
            use_knowledge_base,
            api_key,
            candidate_tools,
            user_preferences,
        )

        client = Groq(api_key=api_key)
        raw_chunks: List[str] = []

        completion_stream = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages + [{"role": "system", "content": FINAL_ANSWER_INSTRUCTION}],
            reasoning_effort="low",
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            temperature=0.4,
            response_format={"type": "json_object"},
            tool_choice="none",
            stream=True,
        )
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

        validated = _prepare_reply(user_message, normalized, _make_retry(client, messages), history)
        validated["kb_sources"] = kb_sources
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
                validated = _prepare_reply(user_message, normalized, _make_retry(client, messages), history)
                validated["kb_sources"] = kb_sources
                validated["tools_used"] = tool_names_used
                yield ("delta", validated["summary"])
                yield ("done", validated)
                return
            except Exception as retry_error:
                e = retry_error
        print(f"[groq] streamed reply failed: {type(e).__name__}")
        yield ("done", _fallback_reply(GENERATION_FAILURE_MESSAGE))
    finally:
        # Runs on every exit path: normal completion, the fallback above, AND
        # an early GeneratorExit from the caller closing us — release()/close()
        # is a documented no-op if the response was already read to
        # completion, so this is safe to always call rather than tracking
        # whether it's "necessary" in each branch.
        if completion_stream is not None:
            completion_stream.close()
