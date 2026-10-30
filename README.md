# Voya AI — Travel Copilot Platform

Voya AI is a full-stack AI travel assistant. Users chat in natural language and get personalized, day-by-day trip itineraries grounded in travel documents, seasonal climate references and estimated budget allocations. The assistant remembers travel preferences across sessions and progressively reveals validated replies in the chat interface.

## Tech Stack

**Frontend** — React 19, TypeScript, Vite, Tailwind CSS, Framer Motion, Zustand, React Router, React Hook Form + Zod, Vitest + React Testing Library.

**Backend** — FastAPI, SQLAlchemy + Alembic, PostgreSQL, Redis, Pinecone (vector search / RAG), Groq API (LLM + function-calling), PyJWT, Argon2, pytest.

## Project Structure

```
voya-ai/
├── backend/
│   ├── main.py              # FastAPI app entrypoint, CORS, startup checks
│   ├── db/                  # SQLAlchemy models, session, schema bootstrap
│   ├── alembic/              # Database migrations
│   ├── routes/               # API endpoints (auth, chat)
│   ├── services/             # Business logic (Groq, Pinecone, tools, preferences, tokens)
│   ├── schemas/               # Pydantic request/response models
│   └── tests/                 # pytest suite
├── frontend/
│   ├── src/
│   │   ├── components/        # UI components (chat thread, composer, sidebar, etc.)
│   │   ├── pages/              # Route-level pages (login, signup, settings, ...)
│   │   ├── hooks/               # Zustand stores (app state, auth state)
│   │   ├── lib/                  # API client
│   │   └── types/                 # Shared TypeScript types
│   └── tests/                      # Vitest suite
└── docker-compose.yml          # Local Redis
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL (a running instance/database you can point `DATABASE_URL` at)
- Docker (for running Redis locally via `docker-compose.yml`)
- API keys: [Groq](https://console.groq.com/) and [Pinecone](https://www.pinecone.io/)

## Environment Variables

### Backend — `backend/.env`

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string. |
| `JWT_SECRET_KEY` | Yes | Secret used to sign access tokens. |
| `GROQ_API_KEY` | Yes | Groq API key, used for LLM chat completion and tool-calling. |
| `PINECONE_API_KEY` | Yes | Pinecone API key, used for the travel knowledge-base vector search. |
| `PINECONE_INDEX_HOST` | Yes | Host URL of your Pinecone index. |
| `REDIS_URL` | No | Defaults to `redis://localhost:6379/0` (matches `docker-compose.yml`). |
| `FRONTEND_ORIGIN` | No | Comma-separated allowed CORS origins. Defaults to common local Vite ports. |
| `APP_ENV` | No | Defaults to `development`. Set to `production` to enable secure (`HTTPS`-only) cookies. |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | No | Needed only for password-reset emails. |

### Frontend — `frontend/.env.local`

| Variable | Required | Description |
|---|---|---|
| `VITE_API_BASE_URL` | No | Defaults to `http://127.0.0.1:8000`. Override if the backend runs elsewhere. |

## Setup & Run

### 1. Start Redis

```bash
docker compose up -d redis
```

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt

# Create backend/.env with the variables listed above, then run migrations
# for the Alembic-managed tables (users, preferences):
alembic upgrade head

uvicorn main:app --reload --port 8000
```

`conversations`/`chat_history` don't need a migration — they're created/kept in sync automatically on startup (see `db/bootstrap.py`).

Check it's up: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints (typically [http://localhost:5173](http://localhost:5173)).

## Running Tests

```bash
# Backend
cd backend
pytest

# Frontend
cd frontend
npm test
```

## Key Features

- SSE chat delivery with a typewriter reveal for validated summaries, itineraries and budget allocations; validation finishes before text becomes visible
- LLM function-calling for seasonal climate references, estimated budgets, and knowledge-base places
- Hard total budget caps, exact category allocations, and explicit notices that admission fees and actual prices need confirmation
- A single server-calculated budget breakdown; generated itinerary prose does not invent separate meal, hotel or transfer quotes
- Sourced venue-location checks for a small curated catalogue, with corrections that preserve requested stops
- Attraction-addition edits compare named visits with the previous itinerary so unchanged or merely reworded plans are rejected
- Retrieval-augmented itinerary suggestions from a Pinecone-backed travel knowledge base
- Persistent, per-account travel-preferences memory (destinations, budget, hotel/transport/food style)
- JWT + CSRF-protected authentication with Redis-backed rate limiting and session revocation
- Account-scoped conversation history ("Recent Chats")

## Travel knowledge ingestion and checks

Run these from `backend/` with the virtual environment active:

```bash
python -m services.document_ingest docs
python -m scripts.eval_kb --cases tests/eval_rag_cases.json
```

Ingestion stages a fresh document version, waits until all new records can be
fetched, and only then prunes the old version. Empty extraction, upload failure,
or a visibility timeout preserves the old version. Local jobs use a file lock;
run jobs sequentially if multiple deployment hosts share the same index.

Text PDFs retain page metadata. Scanned pages use local Windows OCR when its
language pack is available; Linux needs `poppler-utils` and `tesseract-ocr`
with English language data. No document is sent to a remote OCR service.
OCR output is marked unverified because table columns can lose alignment.

The bundled `Train-Timetable.pdf.text.json` is a **partial, visually reviewed
transcription** of selected Coastal Line rows from the dated **2025-12-02**
notice. It is tied to the PDF's SHA-256 fingerprint; changing the PDF requires
reviewing/regenerating the companion text. It is not a complete timetable or
a live service check, and does not establish a Colombo–Kandy departure.

Chat and retrieval tools share evidence filtering: similarity, meaningful text
overlap, named-venue matching and duplicate removal. `KB_MIN_SCORE` defaults to
`0.25` as a retrieval guard, not a confidence percentage. Tune it using positive
and unsupported cases for your documents. Sources list retrieved references;
retrieval alone does not prove every generated sentence is supported.

Factual questions, attraction lists and preference acknowledgements use plain
answers rather than itinerary days or trip-overview cards. Preference-only
messages skip public KB searches, and factual enquiries are not learned as
personal travel preferences. Named-place evidence must match the requested
venue even when the prompt also says "do not invent". Unsupported factual
questions return an explicit verification limit with no source chips. The
current KB does not contain maintained official visa rules, so visa enquiries
ask for nationality/purpose/dates and direct the user to the official authority
without guessing eligibility, validity periods or visa-on-arrival rules.

Completion requests use one bounded retry for temporary rate-limit/server
errors, honouring `Retry-After` up to a 20-second automatic cooldown. Longer
cooldowns return a clear retry message instead of retrying early. Streaming
cooldowns yield cancellation checkpoints. Provider timeouts and rate limits
have separate safe messages and `error_code`/`retry_after_seconds` metadata;
failed generations are not saved as completed chat exchanges or learned as
preferences. Backend diagnostics record the stage, exception type, HTTP status
and retry delay without printing provider error bodies or private prompts.

Itineraries disclose provisional starting points, traveller counts, nights and
missing travel dates in a progressively revealed **Planning assumptions** section.
User-stated details override defaults and persist through follow-up edits; assumed
values never become user-stated facts. A request not to assume keeps missing
details unknown. Close spelling drift in requested destination names (for example,
Kandy becoming Kanda in an activity) triggers validation and a correction attempt,
without silently rewriting place names supplied by the user.

Budget allocations identify their source as the user's stated trip cap, a confirmed
saved budget preference, or a suggested estimate. Generated estimates are never
learned as the user's budget preference. New explicit chat budgets and manual
Settings saves establish confirmed budget provenance; legacy unmarked ranges are
retained in storage but excluded from personalization until confirmed. Saved
preferences are advisory, not new hard caps. Day trips with zero overnight stays
have no accommodation allowance; other category allowances still sum exactly.

Pinecone private memory uses the authenticated account identity, independently
of the browser session. Anonymous requests do not read/write persistent vector
memory. Old browser-session memory namespaces are retained but never read or
automatically migrated because their ownership cannot be established safely.

The extended evaluation covers routing, expected sources and unsupported queries;
`--direct` bypasses routing for diagnosis. Mocked regressions also cover both chat
transports, account switching, provider outages and failed ingestion. These are
not a substitute for checking generated answers against current venue/operator
information.
