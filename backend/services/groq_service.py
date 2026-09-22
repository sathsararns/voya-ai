import os
from typing import Dict, List, Optional

from groq import Groq
from dotenv import load_dotenv

from json_utils import normalize_chat_response
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
        # use_memory/use_knowledge_base come from the context_router
        # decision — when it decides a source isn't relevant to this
        # message, skip that lookup entirely rather than querying and
        # discarding it. Memory (pinecone_memory.py) and the knowledge base
        # (services/kb_rag.py) live in the same Pinecone index but separate
        # namespaces, so these two calls never touch each other's data.
        memory_context = format_memory_context(session_id, user_message, top_k=3) if use_memory else ""

        # One KB search, reused for both the prompt text (kb_context) and
        # the UI source citations (kb_sources) — avoids a second Pinecone
        # call and guarantees the citations shown to the user are exactly
        # the chunks that actually grounded the answer.
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

        client = Groq(api_key=api_key)
        content = _call_groq(client, messages, temperature=0.4)
        normalized = normalize_chat_response(content)

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

        validated = validate_ai_response(user_message, normalized, retry_with_feedback)
        validated["kb_sources"] = kb_sources
        return validated

    except Exception as e:
        return {
            "destination": None,
            "days": None,
            "budget_lkr": None,
            "summary": f"Groq error: {str(e)}",
            "itinerary": [],
            "follow_up_question": "Please try again.",
            "kb_sources": [],
        }
