"""Travel tools the assistant can call: weather, places, budget, itinerary.

These are genuine function-calling tools in the OpenAI/Groq sense (TOOL_SCHEMAS
is handed to Groq's `tools` param — see services/groq_service.py's
_run_tool_calling_phase) — the model decides whether to call any of them, and
if it does, execute_tool_call() runs the real Python function server-side and
the result is fed back into the model's context before it writes its final
answer.

Two of the four (search_places, generate_itinerary) are grounded in the real
Pinecone knowledge base (services/kb_rag.py) — the same data source the app
already uses for factual travel Q&A. The other two (get_weather,
calculate_budget) are deterministic, no external API involved: there's no
live weather or pricing service configured for this project (no API key for
one exists anywhere in this codebase), so get_weather returns curated
reference climate data — clearly labeled as such (`is_live_forecast: False`)
so the model never claims to know an actual forecast — and calculate_budget
is a plain arithmetic breakdown. Both are still real, useful "tools" in the
sense that matters here: the model doesn't have this information memorized
reliably or consistently, and calling the tool gets a correct, structured
answer instead of the model guessing.

Every tool function takes plain keyword arguments (matching its JSON Schema
in TOOL_SCHEMAS) and returns a plain, JSON-serializable dict — never raises;
execute_tool_call() is the one place that catches anything a tool itself
might throw, so a single bad/malformed tool call can't take down the rest of
the turn.
"""

from typing import Any, Callable, Dict, List, Optional

from services.kb_rag import search_knowledge_base
from services.budget import calculate_budget

# --- get_weather --------------------------------------------------------
#
# Reference seasonal climate data, not a live forecast. Sri Lanka's climate
# is dominated by two monsoons (southwest, roughly May-Sept, and northeast,
# roughly Oct-Jan), so "best months" differ by coast/region — this is common
# travel-planning knowledge, not a live data feed.

SRI_LANKA_CLIMATE_REFERENCE: Dict[str, Dict[str, Any]] = {
    "kandy": {
        "region": "hill country",
        "avg_temp_c": "20-27",
        "climate": "Mild and cooler than the coast, with frequent light rain year-round.",
        "best_months": ["Jan", "Feb", "Mar", "Jul", "Aug"],
        "rainy_months": ["Oct", "Nov"],
    },
    "nuwara eliya": {
        "region": "hill country",
        "avg_temp_c": "14-20",
        "climate": "Cool, temperate climate — can feel cold at night; pack warm layers.",
        "best_months": ["Jan", "Feb", "Mar", "Apr"],
        "rainy_months": ["Oct", "Nov", "Dec"],
    },
    "ella": {
        "region": "hill country",
        "avg_temp_c": "17-24",
        "climate": "Cool and pleasant hill-country climate, occasional mist.",
        "best_months": ["Jan", "Feb", "Mar", "Jun", "Jul"],
        "rainy_months": ["Oct", "Nov"],
    },
    "colombo": {
        "region": "west coast",
        "avg_temp_c": "26-31",
        "climate": "Hot and humid year-round; heaviest rain from the southwest monsoon.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun", "Jul", "Sep", "Oct"],
    },
    "negombo": {
        "region": "west coast",
        "avg_temp_c": "26-31",
        "climate": "Hot, humid coastal climate; southwest monsoon brings the heaviest rain.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun", "Jul"],
    },
    "galle": {
        "region": "south coast",
        "avg_temp_c": "26-30",
        "climate": "Warm and humid, driest from December to March.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun", "Oct"],
    },
    "mirissa": {
        "region": "south coast",
        "avg_temp_c": "26-30",
        "climate": "Warm and humid, best conditions align with the dry southwest season.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun", "Oct"],
    },
    "bentota": {
        "region": "south coast",
        "avg_temp_c": "26-30",
        "climate": "Warm and humid beach climate, driest December to March.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun"],
    },
    "hikkaduwa": {
        "region": "south coast",
        "avg_temp_c": "26-30",
        "climate": "Warm and humid, similar seasonality to Galle/Mirissa.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun"],
    },
    "matara": {
        "region": "south coast",
        "avg_temp_c": "26-30",
        "climate": "Warm and humid coastal climate.",
        "best_months": ["Dec", "Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun"],
    },
    "sigiriya": {
        "region": "cultural triangle / dry zone",
        "avg_temp_c": "27-33",
        "climate": "Hot and dry for much of the year.",
        "best_months": ["Jan", "Feb", "Mar", "Jun", "Jul", "Aug"],
        "rainy_months": ["Oct", "Nov", "Dec"],
    },
    "dambulla": {
        "region": "cultural triangle / dry zone",
        "avg_temp_c": "27-33",
        "climate": "Hot and dry, similar to Sigiriya.",
        "best_months": ["Jan", "Feb", "Mar", "Jun", "Jul"],
        "rainy_months": ["Oct", "Nov"],
    },
    "anuradhapura": {
        "region": "dry zone",
        "avg_temp_c": "27-33",
        "climate": "Hot and dry most of the year.",
        "best_months": ["Jan", "Feb", "Mar"],
        "rainy_months": ["Oct", "Nov", "Dec"],
    },
    "polonnaruwa": {
        "region": "dry zone",
        "avg_temp_c": "27-33",
        "climate": "Hot and dry, driest outside the northeast monsoon.",
        "best_months": ["Jan", "Feb", "Mar"],
        "rainy_months": ["Oct", "Nov", "Dec"],
    },
    "jaffna": {
        "region": "north / dry zone",
        "avg_temp_c": "27-32",
        "climate": "Hot and dry; rainfall concentrated in the northeast monsoon.",
        "best_months": ["Jan", "Feb", "Mar", "Apr", "May"],
        "rainy_months": ["Nov", "Dec"],
    },
    "trincomalee": {
        "region": "east coast",
        "avg_temp_c": "26-31",
        "climate": "Hot; driest May-September (opposite season from the west/south coasts).",
        "best_months": ["May", "Jun", "Jul", "Aug", "Sep"],
        "rainy_months": ["Nov", "Dec"],
    },
    "arugam bay": {
        "region": "east coast",
        "avg_temp_c": "26-31",
        "climate": "Hot; best conditions May-September, wetter October-January.",
        "best_months": ["May", "Jun", "Jul", "Aug"],
        "rainy_months": ["Nov", "Dec", "Jan"],
    },
    "batticaloa": {
        "region": "east coast",
        "avg_temp_c": "26-31",
        "climate": "Hot; driest May-September.",
        "best_months": ["May", "Jun", "Jul", "Aug"],
        "rainy_months": ["Nov", "Dec"],
    },
    "kalpitiya": {
        "region": "northwest coast",
        "avg_temp_c": "26-31",
        "climate": "Hot and dry; windy season (kitesurfing) May-September.",
        "best_months": ["May", "Jun", "Jul", "Aug"],
        "rainy_months": ["Oct", "Nov"],
    },
    "yala": {
        "region": "dry zone / southeast",
        "avg_temp_c": "27-32",
        "climate": "Hot and dry; best wildlife viewing in the dry season.",
        "best_months": ["Feb", "Mar", "Apr", "Jun", "Jul"],
        "rainy_months": ["Oct", "Nov"],
    },
    "udawalawe": {
        "region": "dry zone",
        "avg_temp_c": "27-32",
        "climate": "Hot and dry most of the year; good year-round for wildlife.",
        "best_months": ["Jan", "Feb", "Mar", "Jun", "Jul"],
        "rainy_months": ["Oct", "Nov"],
    },
    "kitulgala": {
        "region": "wet zone / hill fringe",
        "avg_temp_c": "23-29",
        "climate": "Warm and humid rainforest climate, with rain possible year-round.",
        "best_months": ["Jan", "Feb", "Mar"],
        "rainy_months": ["May", "Jun", "Oct", "Nov"],
    },
}

GENERIC_SRI_LANKA_CLIMATE: Dict[str, Any] = {
    "region": "Sri Lanka (general)",
    "avg_temp_c": "26-30",
    "climate": "Tropical overall — conditions vary significantly by region and elevation.",
    "best_months": ["Jan", "Feb", "Mar"],
    "rainy_months": ["Oct", "Nov"],
}


def get_weather(destination: str, days: Optional[int] = None) -> Dict[str, Any]:
    """Typical seasonal climate for a Sri Lankan destination.

    Not a live forecast (this project has no weather API integration) —
    `is_live_forecast: False` is included in the result specifically so the
    model never represents this as "today's" or "next week's" weather.
    """
    key = (destination or "").strip().lower()
    data = SRI_LANKA_CLIMATE_REFERENCE.get(key, GENERIC_SRI_LANKA_CLIMATE)
    return {
        "destination": destination,
        "days": days,
        "is_live_forecast": False,
        **data,
    }


# --- search_places -------------------------------------------------------


def search_places(destination: str, category: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
    """Real attractions/places for a destination, from the travel knowledge
    base (services/kb_rag.py) — the same Pinecone-backed data source the app
    already uses for factual travel Q&A, just queried on the model's own
    request instead of context_router's always-on KB path.
    """
    limit = max(1, min(int(limit), 10))
    query = f"{category} in {destination}" if category else f"top tourist attractions in {destination}"
    hits = search_knowledge_base(query, top_k=limit)

    places = []
    for hit in hits:
        fields = hit.get("fields", {})
        name = (fields.get("title") or fields.get("document_name") or "").strip() or "Unnamed place"
        text = (fields.get("text") or "").strip()
        places.append({"name": name, "description": text[:200], "score": hit.get("score")})

    return {"destination": destination, "category": category, "places": places}


# --- calculate_budget ------------------------------------------------------

# --- generate_itinerary ----------------------------------------------------


def generate_itinerary(destination: str, days: int, interests: Optional[List[str]] = None) -> Dict[str, Any]:
    """A day-by-day SKELETON of suggested places, grounded in the knowledge
    base — deliberately not a full itinerary with times/logistics (the
    model's own final JSON `itinerary` field is still what actually produces
    that, per SYSTEM_PROMPT in groq_service.py). This tool's job is only to
    ground that itinerary in real named places instead of the model
    inventing them, by handing back real KB matches distributed round-robin
    across the requested number of days.
    """
    days = max(1, min(int(days), 14))
    queries = list(interests) if interests else ["top attractions"]

    # Deduped by (document_name, chunk_index) — the KB's actual chunk
    # identity — not by the display name below: real ingested documents
    # (see services/kb_rag.py) rarely carry a per-chunk `title`, so multiple
    # genuinely different chunks from the same PDF all fall back to the same
    # `document_name`. Deduping on that display name would collapse them all
    # into a single pooled entry, starving every day after the first of any
    # suggestions at all.
    seen = set()
    pool: List[Dict[str, str]] = []
    for query in queries:
        hits = search_knowledge_base(f"{query} in {destination}", top_k=days * 2)
        for hit in hits:
            fields = hit.get("fields", {})
            document_name = fields.get("document_name") or ""
            chunk_key = (document_name, fields.get("chunk_index"))
            text = (fields.get("text") or "").strip()
            if not text or chunk_key in seen:
                continue
            seen.add(chunk_key)
            name = (fields.get("title") or document_name or "").strip() or "Suggested stop"
            pool.append({"name": name, "note": text[:160]})

    if not pool:
        return {
            "destination": destination,
            "days": days,
            "day_plan": [],
            "note": "No specific knowledge-base matches for this destination — use general travel knowledge instead.",
        }

    day_plan: List[Dict[str, Any]] = [{"day": d, "suggested_places": []} for d in range(1, days + 1)]
    for i, place in enumerate(pool):
        day_plan[i % days]["suggested_places"].append(place)

    return {"destination": destination, "days": days, "day_plan": day_plan}


# --- schemas + dispatch ----------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Typical seasonal climate/weather patterns for a Sri Lankan destination. "
                "Reference data, not a live forecast — use it for general trip-planning "
                "advice (what to pack, best time to visit), not a specific date's forecast."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "description": "The Sri Lankan city/region to check."},
                    "days": {"type": "integer", "description": "Trip length in days, if known."},
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_places",
            "description": "Search the travel knowledge base for real attractions/places in a destination.",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "description": "The destination to search near."},
                    "category": {
                        "type": "string",
                        "description": "Optional category, e.g. 'beaches', 'temples', 'hiking'.",
                    },
                    "limit": {"type": "integer", "description": "Max number of places to return (default 5)."},
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_budget",
            "description": "Estimate a day-by-day travel budget breakdown in LKR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Trip length in days."},
                    "total_budget_lkr": {
                        "type": "integer",
                        "description": "Total budget in LKR, if the user gave one.",
                    },
                    "travel_style": {
                        "type": "string",
                        "enum": ["budget", "mid-range", "luxury"],
                        "description": "Spending style; defaults to mid-range.",
                    },
                },
                "required": ["days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_itinerary",
            "description": (
                "Build a day-by-day skeleton of suggested real places for a destination, "
                "grounded in the travel knowledge base."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string"},
                    "days": {"type": "integer", "description": "Trip length in days."},
                    "interests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional interests, e.g. ['temples', 'wildlife'].",
                    },
                },
                "required": ["destination", "days"],
            },
        },
    },
]

TOOL_FUNCTIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_weather": get_weather,
    "search_places": search_places,
    "calculate_budget": calculate_budget,
    "generate_itinerary": generate_itinerary,
}


def execute_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Runs one requested tool by name. Never raises: an unknown tool name or
    a tool that errors internally (bad/missing arguments, a downstream
    Pinecone failure, etc.) returns a small error dict instead, so one
    bad tool call can't take down the rest of the turn — the calling code
    (see groq_service.py's _run_tool_calling_phase) always gets a
    JSON-serializable result back, exactly as if the tool had "succeeded"
    with a note that it couldn't answer.
    """
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return func(**arguments)
    except Exception as e:
        return {"error": f"{name} failed: {e}"}
