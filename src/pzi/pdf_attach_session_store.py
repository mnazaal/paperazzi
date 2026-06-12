"""Thin in-memory store for browser PDF attach sessions."""

from __future__ import annotations

import time
from collections.abc import Callable

from pzi.pdf_attach_session import AttachSession, mark_attach_session_used


class AttachSessionStore:
    """Small mutable boundary around immutable attach-session records."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._sessions: dict[str, AttachSession] = {}

    def put(self, session: AttachSession) -> None:
        self._prune_expired()
        if session.expires_at <= self._clock():
            return
        self._sessions[session.request_id] = session

    def get(self, request_id: str) -> AttachSession | None:
        self._prune_expired()
        return self._sessions.get(request_id)

    def consume(self, request_id: str) -> AttachSession | None:
        self._prune_expired()
        session = self._sessions.pop(request_id, None)
        if session is None:
            return None
        return mark_attach_session_used(session)

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [
            request_id
            for request_id, session in self._sessions.items()
            if session.expires_at <= now or session.used
        ]
        for request_id in expired:
            self._sessions.pop(request_id, None)
