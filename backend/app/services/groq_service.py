import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


def get_groq_reply(user_message: str) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    client = Groq(api_key=api_key)

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are Voya AI, a travel assistant. "
                    "Return ONLY valid JSON. No markdown. No explanation. No extra text. "
                    "Use this exact structure: "
                    "{"
                    '"destination": string or null, '
                    '"days": number or null, '
                    '"budget_lkr": number or null, '
                    '"summary": string, '
                    '"itinerary": ['
                    '{'
                    '"day": number, '
                    '"title": string, '
                    '"items": [string, string]'
                    '}'
                    '], '
                    '"follow_up_question": string or null'
                    "}"
                ),
            },
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