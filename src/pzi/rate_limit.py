"""Per-host outbound rate limiting for metadata APIs.

A minimum-interval gate keyed by hostname: before each request to a host the
caller blocks until at least the host's polite interval has elapsed since the
previous request to that host.  This complements the reactive 429/Retry-After
handling in :mod:`pzi.fetch_helpers` with a proactive, courteous request spacing
tuned per provider.

The defaults match each API's documented polite-pool / keyless guidance; they
are intentionally conservative so pzi stays a good citizen on shared APIs.
Stdlib only; no global state (instantiate one limiter per service run).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlsplit

# Minimum seconds between consecutive requests to each host.  Keyed by a hostname
# suffix; the longest matching suffix wins.
_DEFAULT_INTERVALS: dict[str, float] = {
    "api.crossref.org": 0.6,          # polite pool ~100 req/min
    "api.openalex.org": 0.6,          # polite pool ~100 req/min
    "dblp.org": 2.0,                  # ~30 req/min
    "api.openreview.net": 2.0,        # ~30 req/min
    "api.semanticscholar.org": 6.0,   # keyless ~10 req/min
}

_FALLBACK_INTERVAL = 0.5


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _interval_for(host: str, intervals: dict[str, float]) -> float:
    best: float | None = None
    best_len = -1
    for suffix, value in intervals.items():
        if (host == suffix or host.endswith("." + suffix)) and len(suffix) > best_len:
            best, best_len = value, len(suffix)
    return best if best is not None else _FALLBACK_INTERVAL


class RateLimiter:
    """Block per-host so consecutive requests respect a minimum interval."""

    def __init__(
        self,
        *,
        intervals: dict[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._intervals = dict(_DEFAULT_INTERVALS if intervals is None else intervals)
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, url: str) -> None:
        """Sleep if needed so the request to *url*'s host is politely spaced."""
        host = _host_of(url)
        if not host:
            return
        interval = _interval_for(host, self._intervals)
        now = self._clock()
        last = self._last.get(host)
        if last is not None:
            elapsed = now - last
            if elapsed < interval:
                self._sleep(interval - elapsed)
                now = self._clock()
        self._last[host] = now
