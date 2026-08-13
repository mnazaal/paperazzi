"""Opt-in on-disk cache for metadata-API responses.

A tiny content cache keyed by request URL, used to avoid re-hitting Crossref /
OpenAlex / DBLP / OpenReview / Semantic Scholar for the same lookup across runs.
Disabled unless ``metadata_cache_ttl`` (seconds) is set in config; the cache
stores the raw response *text* so it composes with the existing
``fetch_text`` → JSON → normalize pipeline.

Corrupt or unreadable entries are treated as misses, never errors.
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

from pzi.fileio import fsync_parent_dir

#: Upper bound on cached responses. Generous — a metadata response is a few KB,
#: so this is tens of MB at worst — but bounded, which the cache was not.
_MAX_ENTRIES = 5000


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

    def _path_for(self, url: str, scope: str = "") -> Path:
        # *scope* covers everything other than the URL that changes the
        # response. The key was the URL alone, while the caller binds an
        # `api_key` and a polite-pool `user_agent` — so a Semantic Scholar
        # lookup made anonymously and the same lookup made with a key shared one
        # entry, and an anonymous-quota answer could be served to an
        # authenticated caller for the whole TTL. Hashed together, so no
        # credential reaches a filename.
        digest = hashlib.sha256(f"{scope}\0{url}".encode()).hexdigest()
        return self._dir / f"{digest}.json"

    def get(self, url: str, scope: str = "") -> str | None:
        """Return cached text for *url*, or None on miss / expiry / corruption."""
        if not self.enabled:
            return None
        path = self._path_for(url, scope)
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

    def set(self, url: str, text: str, scope: str = "") -> None:
        """Store *text* for *url*.  Best-effort: write failures are swallowed."""
        if not self.enabled:
            return
        path = self._path_for(url, scope)
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
            fsync_parent_dir(path)
            # After the write, so the bound holds over the resulting directory
            # rather than the one before it.
            self.sweep()
        except OSError:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)

    def sweep(self) -> None:
        """Drop expired entries, and the oldest ones once the cache is too big.

        Entries were reclaimed only by a ``get()`` on that exact URL, so a
        directory the README calls "small" grew without bound: a lookup never
        repeated is never expired. Swept on write, which is the only moment the
        cache grows, and cheaply — a stat per file, no reads.
        """
        try:
            entries = [(p.stat().st_mtime, p) for p in self._dir.glob("*.json")]
        except OSError:
            return
        now = self._clock()
        keep: list[tuple[float, Path]] = []
        for mtime, path in entries:
            if now - mtime > self._ttl:
                with contextlib.suppress(OSError):
                    path.unlink()
            else:
                keep.append((mtime, path))
        if len(keep) <= _MAX_ENTRIES:
            return
        keep.sort()
        for _mtime, path in keep[: len(keep) - _MAX_ENTRIES]:
            with contextlib.suppress(OSError):
                path.unlink()
