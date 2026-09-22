import os
from typing import Dict, List, Optional

from groq import Groq
from dotenv import load_dotenv

from json_utils import normalize_chat_response
from pinecone_memory import format_memory_context

load_dotenv()

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


def get_groq_reply(
    user_message: str,
    session_id: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_memory: bool = True,
) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    try:
        # use_memory comes from the context_router decision — when the
        # router decides Pinecone memory isn't relevant to this message,
        # skip the lookup entirely rather than querying and discarding it.
        memory_context = format_memory_context(session_id, user_message, top_k=3) if use_memory else ""

        system_prompt = SYSTEM_PROMPT
        if memory_context:
            system_prompt += f"\n{memory_context}\n"

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            reasoning_effort="low",
            max_tokens=1024,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        return normalize_chat_response(content)

    except Exception as e:
        return {
            "destination": None,
            "days": None,
            "budget_lkr": None,
            "summary": f"Groq error: {str(e)}",
            "itinerary": [],
            "follow_up_question": "Please try again.",
        }
