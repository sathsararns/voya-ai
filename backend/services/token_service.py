"""JWT access tokens, delivered in an httpOnly cookie — never read or stored
by frontend JS, never put in localStorage.

Two things beyond plain JWT verification back the "invalidate the session
properly" requirement, since a stateless JWT can't normally be revoked
before it expires:

1. Logout blocklist (Redis): logout adds the token's `jti` to Redis with a
   TTL equal to the token's remaining lifetime. Every authenticated request
   checks the blocklist first — cheap (one Redis GET) and self-cleans (the
   blocklist entry expires exactly when the token would have anyway, so it
   never grows unbounded).
2. Password-change fencing: every token embeds `pwd_changed_at` (the user's
   password_changed_at at the moment the token was issued, as a Unix
   timestamp). On every request, that claim is compared against the user's
   CURRENT password_changed_at from the database — if the user has changed
   their password since this token was issued (e.g. via forgot-password),
   the token is rejected even though it hasn't expired. This transparently
   invalidates every other outstanding session on a password reset, without
   needing to track every issued token individually.
"""

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from dotenv import load_dotenv
from redis import Redis

load_dotenv()

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is missing")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days

ACCESS_TOKEN_COOKIE_NAME = "voya_access_token"
CSRF_COOKIE_NAME = "voya_csrf_token"


def _blocklist_key(jti: str) -> str:
    return f"jwt_blocklist:{jti}"


def create_access_token(user_id: str, email: str, password_changed_at: datetime) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "pwd_changed_at": int(password_changed_at.replace(tzinfo=timezone.utc).timestamp()),
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


class TokenError(Exception):
    """Raised for any invalid/expired/revoked token — routes.auth maps this
    to a single 401, deliberately not distinguishing the reason to the
    client (don't help an attacker tell "expired" from "tampered").
    """


def decode_access_token(token: str, redis_client: Redis) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e

    if redis_client.exists(_blocklist_key(payload["jti"])):
        raise TokenError("token has been revoked")

    return payload


def blocklist_token(redis_client: Redis, payload: dict) -> None:
    """Revoke one token immediately (logout). TTL matches whatever time was
    actually left on the token, so the blocklist entry never outlives it.
    """
    remaining = payload["exp"] - int(time.time())
    if remaining > 0:
        redis_client.setex(_blocklist_key(payload["jti"]), remaining, "1")


def generate_csrf_token() -> str:
    return uuid.uuid4().hex


def access_token_cookie_kwargs(secure: bool) -> dict:
    """Shared cookie attributes for the JWT cookie: httpOnly (never readable
    by JS — this is what keeps the token out of reach of an XSS payload,
    the whole reason to prefer this over localStorage), SameSite=Lax
    (blocks it being sent on cross-site POSTs, the first line of CSRF
    defense), Secure in production (HTTPS-only transmission).
    """
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "max_age": ACCESS_TOKEN_TTL_SECONDS,
        "path": "/",
    }


def csrf_token_cookie_kwargs(secure: bool) -> dict:
    """The CSRF cookie is deliberately NOT httpOnly — the frontend has to be
    able to read it to echo it back as a header (the "double-submit cookie"
    pattern: a cross-site request can make the browser attach the cookie
    automatically, but it can't read the cookie's value to also set the
    matching header, so a mismatch means the request didn't originate from
    a page that could read the app's own cookies).
    """
    return {
        "httponly": False,
        "secure": secure,
        "samesite": "lax",
        "max_age": ACCESS_TOKEN_TTL_SECONDS,
        "path": "/",
    }
