import os
from typing import Dict, Iterator, List, Optional, Tuple

from groq import Groq
from dotenv import load_dotenv

from json_utils import SummaryStreamExtractor, normalize_chat_response
from pinecone_memory import format_memory_context
from services.kb_rag import build_kb_sources, format_kb_context, search_knowledge_base
from services.response_validator import validate_ai_response

load_dotenv()

GROQ_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are Voya AI, a travel assistant.\n"
    "Respond with ONLY one valid JSON object. No markdown, no code fences, "
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
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        reasoning_effort="low",
        max_tokens=1024,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


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
    }


def _build_prompt_messages(
    user_message: str,
    session_id: Optional[str],
    history: Optional[List[Dict[str, str]]],
    use_memory: bool,
    use_knowledge_base: bool,
) -> Tuple[List[Dict[str, str]], List[dict]]:
    """Shared setup for both get_groq_reply and stream_groq_reply: the
    memory/KB lookups, the system prompt, and the final message list handed
    to Groq. Kept in one place so streaming and non-streaming replies are
    always grounded in identical context.
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

    system_prompt = SYSTEM_PROMPT
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

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    return messages, kb_sources


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


def get_groq_reply(
    user_message: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_memory: bool = True,
    use_knowledge_base: bool = True,
) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    try:
        messages, kb_sources = _build_prompt_messages(
            user_message, session_id, history, use_memory, use_knowledge_base
        )

        client = Groq(api_key=api_key)
        content = _call_groq(client, messages, temperature=0.4)
        normalized = normalize_chat_response(content)

        validated = validate_ai_response(user_message, normalized, _make_retry(client, messages))
        validated["kb_sources"] = kb_sources
        return validated

    except Exception as e:
        return _fallback_reply(f"Groq error: {str(e)}")


def stream_groq_reply(
    user_message: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_memory: bool = True,
    use_knowledge_base: bool = True,
) -> Iterator[Tuple[str, object]]:
    """Same reply pipeline as get_groq_reply (same prompt, same
    normalize/validate/retry, same kb_sources), but as it happens: yields
    ("delta", text) for each newly-available fragment of the visible
    `summary` field as the model generates it, ("tick", None) for every
    other raw chunk from Groq (one with no visible text in it — see below
    for why these matter too), followed by exactly one terminal
    ("done", validated_dict) — never raises.

    Only the `summary` field is streamed live (see
    json_utils.SummaryStreamExtractor for why) — the rest of the reply
    (itinerary, destination, budget, kb_sources, and the final,
    validation-corrected summary if a retry was needed) is only available,
    same as before, once the whole object is complete, in the ("done", ...)
    payload. The caller (routes/chat.py) treats that payload as the one
    source of truth for what actually gets saved — this generator never
    doubles as the save path itself, so streaming vs. non-streaming always
    persist identically.

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
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        yield ("done", _fallback_reply("Groq error: GROQ_API_KEY is missing"))
        return

    completion_stream = None
    try:
        messages, kb_sources = _build_prompt_messages(
            user_message, session_id, history, use_memory, use_knowledge_base
        )

        client = Groq(api_key=api_key)
        extractor = SummaryStreamExtractor()
        raw_chunks: List[str] = []

        completion_stream = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            reasoning_effort="low",
            max_tokens=1024,
            temperature=0.4,
            response_format={"type": "json_object"},
            stream=True,
        )
        for chunk in completion_stream:
            delta = chunk.choices[0].delta.content
            if delta:
                raw_chunks.append(delta)
                new_text = extractor.feed(delta)
                if new_text:
                    yield ("delta", new_text)
                    continue

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
        normalized = normalize_chat_response(content)

        validated = validate_ai_response(user_message, normalized, _make_retry(client, messages))
        validated["kb_sources"] = kb_sources
        yield ("done", validated)

    except Exception as e:
        yield ("done", _fallback_reply(f"Groq error: {str(e)}"))
    finally:
        # Runs on every exit path: normal completion, the fallback above, AND
        # an early GeneratorExit from the caller closing us — release()/close()
        # is a documented no-op if the response was already read to
        # completion, so this is safe to always call rather than tracking
        # whether it's "necessary" in each branch.
        if completion_stream is not None:
            completion_stream.close()
