"""Thin in-memory store for browser PDF attach sessions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from pzi.pdf_attach_session import AttachSession, mark_attach_session_used


class AttachSessionStore:
    """Small mutable boundary around immutable attach-session records.

    Thread-safe: the HTTP API runs on a ``ThreadingHTTPServer``, so concurrent
    capture/attach requests can touch the store from different threads.  All
    access is serialized under a lock, and pruning iterates a snapshot so it
    never trips "dict changed size during iteration".
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._sessions: dict[str, AttachSession] = {}
        self._lock = threading.Lock()

    def put(self, session: AttachSession) -> None:
        with self._lock:
            self._prune_expired_locked()
            if session.expires_at <= self._clock():
                return
            self._sessions[session.request_id] = session

    def get(self, request_id: str) -> AttachSession | None:
        with self._lock:
            self._prune_expired_locked()
            return self._sessions.get(request_id)

    def consume(self, request_id: str) -> AttachSession | None:
        with self._lock:
            self._prune_expired_locked()
            session = self._sessions.pop(request_id, None)
        if session is None:
            return None
        return mark_attach_session_used(session)

    def _prune_expired_locked(self) -> None:
        """Drop expired/used sessions. Caller must hold ``self._lock``."""
        now = self._clock()
        expired = [
            request_id
            for request_id, session in list(self._sessions.items())
            if session.expires_at <= now or session.used
        ]
        for request_id in expired:
            self._sessions.pop(request_id, None)
