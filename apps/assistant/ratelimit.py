"""Fixed-window rate limits on the shared cache (Redis in production).

Limits (settings ``ASSISTANT_RATE_LIMITS``) apply per client IP and per salon, the
latter as a cost cap. If the cache is unreachable we *deny* — failing open would
leave LLM spend unbounded.
"""

from __future__ import annotations

import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

WINDOWS = {"minute": 60, "hour": 3600, "day": 86400}


def _hit(key: str, limit: int, window: int, now: float) -> bool:
    bucket = f"rl:{key}:{window}:{int(now // window)}"
    cache.add(bucket, 0, timeout=window + 5)
    return cache.incr(bucket) <= limit


def check(
    *,
    client: str,
    salon_id: int,
    limits: dict[str, dict[str, int]],
    now: float | None = None,
) -> bool:
    """Count one request; ``False`` if any client or salon limit is exceeded."""
    now = time.time() if now is None else now
    try:
        allowed = True
        for scope, ident in (
            ("client", f"{salon_id}:{client}"),
            ("salon", str(salon_id)),
        ):
            for window, limit in limits.get(scope, {}).items():
                # Count every window even after a miss, so bursts can't slip through.
                allowed &= _hit(f"{scope}:{ident}", limit, WINDOWS[window], now)
        return allowed
    except Exception:  # noqa: BLE001 - any cache failure
        logger.exception("Rate-limit cache unavailable; denying assistant request")
        return False
