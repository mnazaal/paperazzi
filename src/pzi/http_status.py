"""Status mapping and request-shape checks shared by the route modules.

Everything here is policy the GET, POST and binary dispatchers must agree on,
so it lives in the one module all three already import rather than being
written out per route.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from pzi import errors
from pzi.config import AppConfig, BibConfig, BibResolutionFailure, load_bib_target

#: HTTP status per structured failure reason — the same vocabulary
#: `pzi.errors.exit_code_for_error` maps to exit codes, so a service
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

    The structured ``reason`` is the whole policy; the message text is the
    user's data, not a classification, and is never read here.

    An unclassified failure takes *default_error_status* rather than a guess.
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


#: Answer to any request body that is not a JSON object. One spelling, on
#: purpose: ten sites said this in six different wordings ("body", "capture
#: body", "attach body", …), and nothing consumes the text — the extension
#: branches on the HTTP status and on `result.status`, never on `error`. Six
#: wordings were six chances for one of them to drift into saying something
#: untrue about a route.
BODY_NOT_AN_OBJECT = "body must be a JSON object"


def require_object(body: Any) -> tuple[int, dict[str, Any]] | None:
    """Reject a request body that is not a JSON object.

    ``None`` when *body* is usable, so a route reads
    ``if (error := require_object(body)) is not None: return error``.
    """
    if isinstance(body, dict):
        return None
    return 400, {"error": BODY_NOT_AN_OBJECT}


def bib_selector_of(body: Mapping[str, Any]) -> str | None:
    """The ``bib`` selector from a request body, or ``None`` if it is not a string.

    A non-string ``bib`` means "the default library" rather than an error: the
    confinement check (`reject_unconfigured_bib_selector`) is what refuses a
    library the config does not declare, and it runs before any route handler.
    """
    selector = body.get("bib")
    return selector if isinstance(selector, str) else None


class JsonError(NamedTuple):
    """A ``(status, body)`` response, tagged so a union can be narrowed.

    It *is* the response tuple a route returns, so a caller passes it straight
    back; the class only exists to make ``isinstance`` able to tell it apart
    from a resolved library.
    """

    status: int
    body: dict[str, Any]


def resolve_bib_or_error(
    *, config_path: str, home_dir: str, bib_selector: str | None
) -> tuple[AppConfig, BibConfig] | JsonError:
    """Resolve one library, or the response to answer a failure with.

    Five routes across three modules spelled out the same ``load_bib_target``
    → ``isinstance(resolved, BibResolutionFailure)`` → ``400`` sequence, each
    rebuilding the error body — five chances for one route to answer a
    resolution failure differently from its neighbours.
    """
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, BibResolutionFailure):
        return JsonError(400, {"status": "error", "errors": resolved.errors})
    return resolved
