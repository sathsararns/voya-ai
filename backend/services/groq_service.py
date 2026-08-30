import os
import json
from groq import Groq
from dotenv import load_dotenv
from pinecone_memory import format_memory_context

load_dotenv()


def get_groq_reply(user_message: str) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    memory_context = format_memory_context(user_message, top_k=3)

    client = Groq(api_key=api_key)

    system_prompt = (
        "You are Voya AI, a travel assistant. "
        "Return ONLY valid JSON. No markdown. No explanation. No extra text. "
        "Use this structure exactly: "
        "{"
        '"destination": string or null, '
        '"days": number or null, '
        '"budget_lkr": number or null, '
        '"summary": string, '
        '"itinerary": ['
        '{'
        '"day": number, '
        '"title": string, '
        '"items": [string]'
        '}'
        '], '
        '"follow_up_question": string or null'
        "}"
    )

    if memory_context:
        system_prompt += f"\n\n{memory_context}"

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        reasoning_effort="low",
        max_tokens=700,
        temperature=0.4,
    )

    content = response.choices[0].message.content

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {
            "destination": None,
            "days": None,
            "budget_lkr": None,
            "summary": content,
            "itinerary": [],
            "follow_up_question": None,
        }