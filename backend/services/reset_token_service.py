"""Password-reset tokens — Redis-backed, single-use, link-based.

Replaces OTP-based password reset. A reset request generates a long random
URL-safe token; only its SHA-256 hash is ever stored in Redis (same
rationale services/otp_service.py used for OTP hashes: a Redis dump or
MONITOR snapshot never reveals a usable token). The raw token is embedded in
the reset-link URL emailed to the user and never stored anywhere itself.

Unlike a 6-digit OTP, a 256-bit random token isn't guessable within its TTL,
so there's no separate "verify attempts" counter here the way otp_service
needed one — possessing the token (i.e. having clicked the emailed link) IS
the proof, once.
"""

import hashlib
import secrets
from typing import Optional

from redis import Redis

RESET_TOKEN_TTL_SECONDS = 30 * 60  # 30 minutes


def _token_key(token_hash: str) -> str:
    return f"pwd_reset_token:{token_hash}"


def _user_token_key(user_id: str) -> str:
    return f"pwd_reset_user:{user_id}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_reset_token(redis_client: Redis, user_id: str) -> str:
    """Generate a new reset token for this user, invalidating any token
    issued earlier for them (only the most recently requested link ever
    works — requesting a new one silently kills the old one). Returns the
    raw token; callers embed it in the emailed reset link and never store it
    themselves.
    """
    previous_hash = redis_client.get(_user_token_key(user_id))
    if previous_hash:
        redis_client.delete(_token_key(previous_hash))

    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)

    pipe = redis_client.pipeline()
    pipe.setex(_token_key(token_hash), RESET_TOKEN_TTL_SECONDS, user_id)
    pipe.setex(_user_token_key(user_id), RESET_TOKEN_TTL_SECONDS, token_hash)
    pipe.execute()

    return token


def consume_reset_token(redis_client: Redis, token: str) -> Optional[str]:
    """Validate and immediately invalidate a reset token (single use).
    Returns the id of the user it was issued for, or None if the token is
    missing, invalid, or expired.
    """
    token_hash = _hash_token(token)
    user_id = redis_client.get(_token_key(token_hash))
    if user_id is None:
        return None

    redis_client.delete(_token_key(token_hash))
    redis_client.delete(_user_token_key(user_id))
    return user_id
