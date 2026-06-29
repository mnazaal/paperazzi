"""Opt-in on-disk cache for metadata-API responses.

A tiny content cache keyed by request URL, used to avoid re-hitting Crossref /
OpenAlex / DBLP / OpenReview / Semantic Scholar for the same lookup across runs.
Disabled unless ``metadata_cache_ttl`` (seconds) is set in config; the cache
stores the raw response *text* so it composes with the existing
``fetch_text`` → JSON → normalize pipeline.

Stdlib only.  Corrupt or unreadable entries are treated as misses, never errors.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


class MetadataCache:
    """URL-keyed text cache with per-entry TTL, backed by one JSON file per key."""

    def __init__(
        self,
        cache_dir: str | Path,
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dir = Path(cache_dir)
        self._ttl = max(0, int(ttl_seconds))
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._dir / f"{digest}.json"

    def get(self, url: str) -> str | None:
        """Return cached text for *url*, or None on miss / expiry / corruption."""
        if not self.enabled:
            return None
        path = self._path_for(url)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        ts = payload.get("ts")
        text = payload.get("text")
        if not isinstance(ts, (int, float)) or not isinstance(text, str):
            return None
        if self._clock() - ts > self._ttl:
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        return text

    def set(self, url: str, text: str) -> None:
        """Store *text* for *url*.  Best-effort: write failures are swallowed."""
        if not self.enabled:
            return
        path = self._path_for(url)
        payload = json.dumps({"url": url, "ts": self._clock(), "text": text})
        tmp: str | None = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=str(self._dir), suffix=".tmp", delete=False, encoding="utf-8"
            ) as f:
                f.write(payload)
                tmp = f.name
            os.replace(tmp, str(path))
        except OSError:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
