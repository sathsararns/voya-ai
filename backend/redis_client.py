"""Shared Redis connection, mirroring how pinecone_memory.py owns the single
Pinecone client — one module-level connection, reused by every service that
needs Redis (password-reset token storage/expiry, rate limiting, JWT logout
blocklist) instead of each service opening its own.

Local dev: `docker compose up -d redis` (see docker-compose.yml) starts a
Redis container on the default REDIS_URL below.
"""

import os

from dotenv import load_dotenv
import redis

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
