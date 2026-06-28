"""Shared error types whose messages are meant for direct display to the user.

Leaf module (no intra-package imports) so any layer can raise these without
risking an import cycle.  The CLI boundary in :mod:`pzi.cli` prints a
``PziError`` verbatim as ``error: <message>`` — so the message must already be
phrased for a human (include the offending path, no tracebacks, no jargon).
"""

from __future__ import annotations


class PziError(Exception):
    """An error carrying a ready-to-display, user-facing message."""
