"""Bounded retries for temporary completion API failures, with safe diagnostics.

The SDK's own retries are disabled by the caller. Stream-opening cooldowns
yield checkpoints so Stop/disconnect can cancel before the next API call.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import time

from groq import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

logger = logging.getLogger(__name__)
MAX_RETRY_WAIT_SECONDS = 20.0
RETRY_CHECKPOINT_SECONDS = 0.25


def retry_after_seconds(error: Exception):
    headers = getattr(getattr(error, "response", None), "headers", {}) or {}
    for name, multiplier in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            delay = float(raw) * multiplier
        except (ValueError, TypeError):
            if name != "retry-after":
                continue
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = (date - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(delay) and delay >= 0:
            return delay
    return None


def transient_retry_delay(error: Exception):
    transient = isinstance(error, RateLimitError) or (
        isinstance(error, APIStatusError) and error.status_code in {408, 409, 500, 502, 503, 504}
    )
    if not transient:
        return None
    delay = retry_after_seconds(error)
    delay = delay if delay is not None else 2.0
    # Never retry earlier than the provider permits. Long/daily limits get
    # a retry message instead of tying up a request for minutes or hours.
    return delay if delay <= MAX_RETRY_WAIT_SECONDS else None


def log_failure(stage: str, error: Exception) -> None:
    logger.warning("completion_failure stage=%s type=%s status=%s retry_after_seconds=%s",
                   stage, type(error).__name__, getattr(error, "status_code", None),
                   retry_after_seconds(error))


def request_completion(client, **kwargs):
    for attempt in range(2):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as error:
            delay = transient_retry_delay(error)
            if attempt or delay is None:
                raise
            log_failure("retry", error)
            time.sleep(delay)


def open_completion_stream(client, **kwargs):
    """Yield ('tick', None) during cooldown, then ('stream', stream)."""
    for attempt in range(2):
        try:
            stream = client.chat.completions.create(**kwargs)
        except Exception as error:
            delay = transient_retry_delay(error)
            if attempt or delay is None:
                raise
            log_failure("stream_retry", error)
            # Fixed slices, rather than a blocking SDK sleep, make waiting
            # cancellable and deterministic to test without real delays.
            while delay > 0:
                yield "tick", None
                pause = min(RETRY_CHECKPOINT_SECONDS, delay)
                time.sleep(pause)
                delay -= pause
        else:
            yield "stream", stream
            return


def failure_details(error: Exception) -> tuple[str, str, int | None]:
    if isinstance(error, RateLimitError):
        delay = retry_after_seconds(error)
        seconds = max(1, math.ceil(delay)) if delay is not None else None
        message = (f"Voya AI is temporarily busy. Please try again in about {seconds} seconds."
                   if seconds is not None else "Voya AI is temporarily busy. Please wait a moment and try again.")
        return "rate_limited", message, seconds
    if isinstance(error, APITimeoutError):
        return "provider_timeout", "The AI service took too long to respond. Please try again shortly.", None
    if isinstance(error, APIConnectionError) or (
        isinstance(error, APIStatusError) and error.status_code >= 500
    ):
        return "provider_unavailable", "The AI service is temporarily unavailable. Please try again shortly.", None
    return "generation_failed", "I couldn't finish that response. Please try your request again.", None
