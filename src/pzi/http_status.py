"""HTTP status mapping for service result dictionaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pzi import errors

#: HTTP status per structured failure reason — the same vocabulary
#: `pzi.commands.common.exit_code_for_error` maps to exit codes, so a service
#: classifies its failure once and both surfaces agree.
_STATUS_BY_REASON: dict[str, int] = {
    errors.REASON_NOT_FOUND: 404,
    errors.REASON_USAGE: 400,
    errors.REASON_CONFIG: 400,
    errors.REASON_UNAVAILABLE: 503,
    errors.REASON_CONFLICT: 409,
}


def status_for_service_result(
    result: Mapping[str, Any],
    *,
    default_error_status: int = 400,
) -> int:
    """Map common service result shapes to HTTP status codes.

    Services return small dicts with ``status``, ``message``, and/or ``errors``.
    Keep mapping here so route modules do not each invent their own policy.

    The structured ``reason`` is the whole policy. Message text used to be
    consulted as a fallback and it was wrong by construction: it tested
    ``"config" in text`` before ``"not found" in text``, and citekeys are echoed
    into these messages, so `POST /tags/add` on a missing entry answered 404 for
    ``nosuch2020`` and 400 for ``myconfig2020`` — the same failure, two
    statuses, decided by how the user happened to name a paper. No amount of
    reordering fixes that; the message is the user's data, not a classification.

    So an unclassified failure takes *default_error_status* rather than a guess.
    That is a real cost — a service that forgets to classify answers 400 where
    404 was right — and it is the intended one: a missing reason is a bug in
    that service with one obvious fix, while a heuristic silently returns a
    plausible wrong answer forever.
    """
    if result.get("status") == "ok":
        return 200

    reason = result.get("reason")
    if isinstance(reason, str) and reason in _STATUS_BY_REASON:
        return _STATUS_BY_REASON[reason]

    return default_error_status


def reject_unconfigured_bib_selector(
    selector: object, *, config: Mapping[str, Any] | None, home_dir: str
) -> tuple[int, dict[str, Any]] | None:
    """Reject a request naming a library *config* does not declare.

    The HTTP API is confined to configured libraries; a direct ``.bib`` path is
    CLI-only. Applied by both dispatchers rather than per route: the POST side
    had this check while every GET and binary route accepted any existing
    ``.bib`` path, so an isolated ``/export/raw?bib=/elsewhere/private.bib``
    returned the contents of a bibliography the config had never heard of.

    Takes an already-loaded *config* so each dispatcher keeps loading it the way
    it already does (and stays independently testable); ``None`` means the
    config could not be loaded, which the handler reports itself.
    """
    from pzi.config import is_configured_selector

    if not isinstance(selector, str) or not selector.strip():
        return None
    if config is None:
        return None
    if is_configured_selector(config.get("bibs") or [], selector, home_dir=home_dir):
        return None
    return 400, {
        "error": (
            "bib must name a library configured in config.toml "
            "(a direct .bib path is CLI-only)"
        )
    }
