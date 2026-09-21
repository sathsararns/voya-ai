import os
import json
from typing import Optional

from groq import Groq
from dotenv import load_dotenv
from pinecone_memory import format_memory_context

load_dotenv()

def get_groq_reply(user_message: str, session_id: Optional[str] = None) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing")

    try:
        memory_context = format_memory_context(session_id, user_message, top_k=3)

        system_prompt = (
            "You are Voya AI, a travel assistant.\n"
            "Reply only in valid JSON.\n"
            "Use this schema:\n"
            "{destination, days, budget_lkr, summary, itinerary, follow_up_question}\n"
        )

        if memory_context:
            system_prompt += f"\nRelevant memory:\n{memory_context}\n"

        client = Groq(api_key=api_key)
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

    except Exception as e:
        return {
            "destination": None,
            "days": None,
            "budget_lkr": None,
            "summary": f"Groq error: {str(e)}",
            "itinerary": [],
            "follow_up_question": "Please try again.",
        }