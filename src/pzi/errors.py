"""Shared error types whose messages are meant for direct display to the user.

Leaf module (imports only :mod:`pzi.exit_codes`, itself import-free) so any
layer can raise these without risking an import cycle.  The CLI boundary in
:mod:`pzi.cli` prints a ``PziError`` as ``error: <message>`` followed by its
details and exits with its ``code`` — so the message must already be phrased for
a human (include the offending path, no tracebacks, no jargon).
"""

from __future__ import annotations

from pzi import exit_codes


class PziError(Exception):
    """An error carrying a ready-to-display message and the exit code to use."""

    def __init__(
        self,
        message: str,
        *,
        code: int = exit_codes.ENVIRONMENT,
        details: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or []
