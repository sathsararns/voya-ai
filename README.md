# Voya AI — Travel Copilot Platform

Voya AI is a full-stack AI travel assistant. Users chat in natural language and get back personalized, day-by-day trip itineraries, with the assistant able to pull in live weather, budget, and points-of-interest data, remember a user's travel preferences across sessions, and stream its replies back token-by-token in real time.

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

- Real-time streaming chat responses (Server-Sent Events) with a typewriter-style reveal
- LLM function-calling for live weather, budget, and places data
- Retrieval-augmented itinerary suggestions from a Pinecone-backed travel knowledge base
- Persistent, per-account travel-preferences memory (destinations, budget, hotel/transport/food style)
- JWT + CSRF-protected authentication with Redis-backed rate limiting and session revocation
- Account-scoped conversation history ("Recent Chats")
