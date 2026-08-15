"""Shared error types whose messages are meant for direct display to the user.

Leaf module (imports only :mod:`pzi.exit_codes`, itself import-free) so any
layer can raise these without risking an import cycle.  The CLI boundary in
:mod:`pzi.cli` prints a ``PziError`` as ``error: <message>`` followed by its
details and exits with its ``code`` — so the message must already be phrased for
a human (include the offending path, no tracebacks, no jargon).
"""

from __future__ import annotations

from collections.abc import Mapping

from pzi import exit_codes

# ---------------------------------------------------------------------------
# Structured failure reasons
#
# A failed service result says *why* in a ``reason`` field, so neither the CLI
# nor the HTTP API has to match on message text. Both mappers
# (:func:`pzi.commands.common.exit_code_for_error` and
# :func:`pzi.http_status.status_for_service_result`) read the same vocabulary,
# which is why it lives here rather than beside either of them: a service adds
# ``"reason": REASON_USAGE`` once and both surfaces agree.
#
# Grepping the human message was the alternative and it was actively wrong —
# ``status_for_service_result`` tested ``"config" in text`` before
# ``"not found" in text``, so a citekey containing the word "config" turned a
# 404 into a 400.
# ---------------------------------------------------------------------------

#: The named entry does not exist (an unknown citekey).
REASON_NOT_FOUND = "not_found"
#: The invocation itself was wrong — a bad flag combination, an argument that
#: normalizes to nothing. The user must retype, not retry.
REASON_USAGE = "usage"
#: Configuration is missing, unreadable, or does not declare what was asked for.
REASON_CONFIG = "config"
#: A dependency the command needs is not reachable right now; retrying later is
#: reasonable.
REASON_UNAVAILABLE = "unavailable"
#: The request is well-formed but conflicts with current state (e.g. a target
#: that already exists).
REASON_CONFLICT = "conflict"


class PziError(Exception):
    """An error carrying a ready-to-display message and the exit code to use."""

    def __init__(
        self,
        message: str,
        *,
        code: int = exit_codes.ENVIRONMENT,
        details: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or []
        #: The structured `REASON_*` discriminator, when the raiser knows it.
        #: The exit code alone cannot stand in for it: `ENVIRONMENT` covers
        #: config, unavailable *and* conflict, so a consumer branching on the
        #: code cannot tell a broken config from a service that is down. Left
        #: `None` where the raiser genuinely does not know, and rendered with a
        #: documented coarse fallback rather than a guess dressed as fact.
        self.reason = reason


_EXIT_CODE_BY_REASON: dict[str, int] = {
    REASON_NOT_FOUND: exit_codes.NOT_FOUND,
    REASON_USAGE: exit_codes.USAGE,
    REASON_CONFIG: exit_codes.ENVIRONMENT,
    REASON_UNAVAILABLE: exit_codes.ENVIRONMENT,
    REASON_CONFLICT: exit_codes.ENVIRONMENT,
}


def exit_code_for_error(result: Mapping[str, object]) -> int:
    """Exit code for a service result that failed.

    Services report *why* they failed in a structured ``reason`` field rather
    than in prose, so a runner never has to match on message text — and a
    message reworded for humans cannot silently change a script's exit code.
    The vocabulary is :mod:`pzi.errors`; ``pzi.http_status`` maps the same
    values to HTTP statuses, so a service that classifies its failure once is
    correct on both surfaces.

    Callers must have already handled the success case: this always returns a
    failure code.
    """
    reason = result.get("reason")
    if isinstance(reason, str):
        return _EXIT_CODE_BY_REASON.get(reason, exit_codes.ENVIRONMENT)
    return exit_codes.ENVIRONMENT
