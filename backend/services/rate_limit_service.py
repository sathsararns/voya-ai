"""Generic fixed-window rate limiter backed by Redis.

One small primitive reused for every auth action that needs a limit:
password-reset link requests and login attempts. Fixed-window (INCR +
EXPIRE) rather than a sliding log — simpler, one round trip, and plenty
precise for "N attempts per M minutes" style limits; it can allow a short
burst right at a window boundary, which is an acceptable trade-off for this
project's threat model (slowing down abuse, not billing-grade metering).
"""

from redis import Redis


def check_and_increment(redis_client: Redis, key: str, max_attempts: int, window_seconds: int) -> bool:
    """Increment the counter at `key` and report whether the caller is still
    within the allowed rate. Returns False once `key` has been incremented
    more than `max_attempts` times inside the current window.

    The first increment on a fresh key also sets its expiry, so the counter
    self-resets after `window_seconds` without any separate cleanup step.
    """
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.ttl(key)
    count, ttl = pipe.execute()

    if ttl == -1:
        # Key had no expiry yet (either brand new, or created by INCR just
        # now without one) — this is the start of a fresh window.
        redis_client.expire(key, window_seconds)

    return count <= max_attempts
