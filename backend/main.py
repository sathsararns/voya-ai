import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError, RedisError
from db.session import engine
from db.bootstrap import run_schema_bootstrap
from redis_client import redis_client
from routes.auth import router as auth_router
from routes.chat import router as chat_router

# Only conversations/chat_history — the `users` table (and anything else
# added after it) is Alembic-managed. See db/bootstrap.py's module
# docstring for why create_all() is scoped instead of touching everything
# registered on Base.metadata.
run_schema_bootstrap(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auth (login/password-reset rate limiting, reset-link storage, and
    # session revocation) depends on Redis — none of that fails until the
    # first request hits it if we don't check here. This makes a
    # missing/unreachable Redis an impossible-to-miss console message at
    # boot, not a silent trap a developer only discovers via a vague 503
    # after they've already started debugging the wrong thing.
    try:
        redis_client.ping()
        print("[startup] Redis connection OK")
    except RedisError as e:
        print("=" * 72)
        print("[startup] WARNING: could not connect to Redis.")
        print(f"          {e!r}")
        print("          Login and forgot/reset-password all depend on Redis and")
        print("          will fail with a 503 until it's reachable. Signup and")
        print("          logout are unaffected. Start it with:")
        print("            docker compose up -d redis")
        print("          (from the project root - see docker-compose.yml).")
        print("          REDIS_URL in backend/.env controls where the app looks")
        print("          for it; the chat/RAG features are unaffected.")
        print("=" * 72)
    yield


app = FastAPI(title="Voya AI", lifespan=lifespan)

# Specific origins (not "*") are required, not just tidier: cookie-based
# auth means the frontend sends requests with credentials: 'include', and
# browsers reject that combination outright if allow_origins is a wildcard.
#
# FRONTEND_ORIGIN accepts a comma-separated list, not just one value — Vite
# auto-increments its port (5173 -> 5174 -> ...) whenever the one it wants
# is already taken by something else on the machine, so a single hardcoded
# origin silently breaks every request (the browser blocks them with
# "Disallowed CORS origin", which surfaces to fetch() as an opaque network
# error) the moment dev's actual port drifts from what's configured here.
# The default list covers Vite's common local fallback range so this works
# out of the box; production should set FRONTEND_ORIGIN to the real
# deployed origin(s) explicitly.
DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176"
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)


# Without this, a Redis outage (auth's rate limiting, reset-token storage,
# and session blocklist all depend on it — see services/reset_token_service.py,
# token_service.py, rate_limit_service.py) surfaces as a bare 500 with no
# `detail`, which the frontend's error handling (see frontend/src/lib/api.ts)
# can only show as a generic "Something went wrong" — indistinguishable from
# an actual bug.
# This turns it into an honest, specific 503 instead.
#
# The client only ever gets the generic message below — deliberately: which
# internal service is down is an infra detail, not something to hand an
# unauthenticated caller. The full exception (and, for a connection failure
# specifically, the same "how to fix it" pointer as the startup check) is
# printed server-side instead, so a developer can see exactly which request
# failed and why without that detail ever reaching the response body.
@app.exception_handler(RedisError)
async def redis_error_handler(request: Request, exc: RedisError):
    print(f"[main] Redis error on {request.method} {request.url.path}: {exc!r}")
    if isinstance(exc, RedisConnectionError):
        print("[main] Redis appears to be unreachable - run `docker compose up -d redis` and retry.")

    return JSONResponse(
        status_code=503,
        content={"detail": "A required service is temporarily unavailable. Please try again shortly."},
    )


@app.get("/health")
def health_check():
    return {"status": "ok", "project": "Voya AI"}