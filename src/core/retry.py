"""Backoff helpers (stdlib only, easy to unit test)."""
import email.utils
import random
from datetime import datetime, timezone
from typing import Callable, Optional


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Retry-After may be delta-seconds or an HTTP-date. Returns seconds or None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def compute_backoff(
    attempt: int,
    base: float = 1.0,
    cap: float = 60.0,
    retry_after: Optional[float] = None,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with equal jitter; never shorter than the server's Retry-After."""
    ceiling = min(cap, base * (2 ** attempt))
    delay = ceiling / 2 + rng() * ceiling / 2
    if retry_after is not None:
        delay = max(delay, min(retry_after, cap * 5))
    return delay
