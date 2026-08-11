"""POST route boundary for the HTTP API.

This module keeps socket/server concerns out of request handling: functions take
plain request data and return ``(status_code, response_dict)`` tuples. Parsing
and planning helpers are pure; endpoint handlers are thin imperative shells
around service calls and injected runtime adapters.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from pzi.add_planning import error_result
from pzi.bib_repository import ConcurrentEditError
from pzi.bib_service import delete_entry
from pzi.bibtex import normalize_authors
from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput, CaptureOptions, PageArtifact, PdfCandidate
from pzi.config import (
    BibResolutionFailure,
    load_bib_target,
    load_config_file,
)
from pzi.http_binary_routes import path_confined_to
from pzi.http_payloads import (
    capture_payload,
    inbox_drain_payload,
    promote_payload,
    tag_change_payload,
    update_payload,
)
from pzi.http_security import DEFAULT_MAX_BODY_BYTES, safe_public_http_url
from pzi.http_status import (
    reject_unconfigured_bib_selector,
    status_for_service_result,
)
from pzi.pdf_acquisition_plan import build_pdf_acquisition_plan
from pzi.pdf_attach_session import build_attach_session, validate_attach_request
from pzi.pdf_attach_session_store import AttachSessionStore
from pzi.pdf_service import attach_pdf_bytes, attach_pdf_raw_bytes
from pzi.promote_service import promote_bib
from pzi.tag_service import add_tags, remove_tags
from pzi.update_service import update_bib

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_PDF_URL_CANDIDATES = 20
#: Politeness delay between inbox items, matching the CLI's `--delay` default
#: (`cli_parser.py`). The HTTP route used to default to 0.
_DEFAULT_INBOX_DELAY_SECONDS = 1.0
#: Ceiling on a client-supplied delay: the drain occupies a server thread for
#: its whole run, so an unbounded value is a self-inflicted denial of service.
_MAX_INBOX_DELAY_SECONDS = 60.0
ATTACH_SESSION_TTL_SECONDS = 600
MAX_BROWSER_PDF_BYTES = DEFAULT_MAX_BODY_BYTES

# A concurrent external edit aborts the write at the repository layer; report it
# as 409 Conflict rather than letting it surface as an opaque 500.
CONCURRENT_EDIT_MESSAGE = (
    "bib file was modified externally while writing — retry the request"
)

# ---------------------------------------------------------------------------
# Capture body helpers
# ---------------------------------------------------------------------------


def body_flag(body: Mapping[str, Any], key: str, *, default: bool) -> bool:
    """Read a JSON boolean from a request body — strictly.

    ``bool(body.get(key, default))`` decides on Python truthiness, which is not
    what a JSON API means. Two consequences were live: ``{"dry_run": null}``
    authorized a *real* update (``bool(None)`` is False, and False means "not a
    preview"), and ``{"replace": "false"}`` selected replace mode, because a
    non-empty string is truthy. JSON has real booleans; anything else is a
    caller mistake, and for a flag that gates a write the safe reading of a
    mistake is the default, not the destructive branch.
    """
    value = body.get(key, default)
    return value if isinstance(value, bool) else default


def record_overrides_from_capture_body(body: dict[str, Any]) -> dict[str, object]:
    record_overrides: dict[str, object] = {}
    raw_tags = body.get("tags")
    if isinstance(raw_tags, list):
        record_overrides["tags"] = [
            tag for tag in raw_tags if isinstance(tag, str) and tag.strip()
        ]
    for body_key, record_key in [
        ("page_title", "fallback_title"),
        ("canonical_url", "fallback_canonical_url"),
        ("source_url", "fallback_source_url"),
        ("abstract_url", "fallback_abstract_url"),
        ("doi", "fallback_doi"),
    ]:
        value = body.get(body_key)
        if isinstance(value, str) and value.strip():
            record_overrides[record_key] = value.strip()
    # Rich embedded metadata from browser extension (Tier 2)
    _maybe_validate_authors_str(body, "embedded_authors", record_overrides, "fallback_authors")
    _maybe_set_fallback_str(body, "embedded_year", record_overrides, "fallback_year")
    _maybe_set_fallback_str(body, "embedded_venue", record_overrides, "fallback_venue")
    _maybe_set_fallback_str(body, "embedded_abstract", record_overrides, "fallback_abstract")
    _maybe_set_fallback_str(body, "embedded_volume", record_overrides, "fallback_volume")
    _maybe_set_fallback_str(body, "embedded_issue", record_overrides, "fallback_issue")
    _maybe_set_fallback_str(body, "embedded_pages", record_overrides, "fallback_pages")
    _maybe_set_fallback_str(body, "embedded_issn", record_overrides, "fallback_issn")
    _maybe_set_fallback_str(body, "embedded_isbn", record_overrides, "fallback_isbn")
    _maybe_set_fallback_str(body, "embedded_pdf_url", record_overrides, "fallback_pdf_url")
    # JSON-LD / OG fallbacks — used when citation_* meta is absent.
    # Order: OG first, then JSON-LD (JSON-LD is more reliable, wins if both present).
    # Where a field also has a citation_* source above, JSON-LD yields to it
    # instead of overwriting — otherwise "used when citation_* is absent" was
    # exactly backwards, and a page carrying both had its citation_author and
    # citation_publication_date replaced by whatever its JSON-LD blob claimed.
    _maybe_set_fallback_str(body, "embedded_og_title", record_overrides, "fallback_title")
    _maybe_validate_authors_str(
        body, "embedded_jsonld_authors", record_overrides, "fallback_authors",
        overwrite=False,
    )
    # Title has no citation_* source — its only competitors are the page <title>
    # and OG, both of which JSON-LD is meant to beat.
    _maybe_set_fallback_str(body, "embedded_jsonld_title", record_overrides, "fallback_title")
    _maybe_set_fallback_str(
        body, "embedded_jsonld_year", record_overrides, "fallback_year", overwrite=False,
    )
    # Promote trusted browser-parsed fields from fallback_* to normal overrides.
    trusted_fields = body.get("trusted_fields")
    if isinstance(trusted_fields, list):
        for field in trusted_fields:
            if not isinstance(field, str) or not field.strip():
                continue
            name = field.strip()
            fallback_key = f"fallback_{name}"
            if fallback_key in record_overrides:
                value = record_overrides.pop(fallback_key)
                if name == "authors" and isinstance(value, str):
                    value = normalize_authors(value)
                record_overrides[name] = value
    return record_overrides


def capture_input_from_http_body(
    body: dict[str, Any],
    *,
    pdf_candidates: list[str] | None,
    value_override: str | None = None,
) -> CaptureInput:
    """Build pure capture input from validated HTTP capture body.

    *value_override* replaces the body's ``url``. The local-file branch uses it
    to capture the *resolved* path its confinement check was made about, rather
    than the caller's spelling of it.
    """
    raw_cookies = body.get("cookies")
    cookies = raw_cookies.strip() if isinstance(raw_cookies, str) and raw_cookies.strip() else None
    raw_page_html = body.get("page_html")
    raw_head_html = body.get("head_html")
    html_for_artifact = (
        raw_page_html if isinstance(raw_page_html, str) and raw_page_html.strip()
        else raw_head_html if isinstance(raw_head_html, str) and raw_head_html.strip()
        else None
    )
    page_artifact = (
        PageArtifact(html=html_for_artifact, source="http")
        if html_for_artifact is not None
        else None
    )
    return CaptureInput(
        value=value_override if value_override is not None else str(body["url"]).strip(),
        record_overrides=record_overrides_from_capture_body(body),
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        pdf_candidates=tuple(
            PdfCandidate(value=value, source="http")
            for value in (pdf_candidates or [])
        ),
        page_artifact=page_artifact,
        auth_hints=AuthHints(cookies=cookies),
    )


def capture_options_from_http_body(
    body: dict[str, Any],
    *,
    config: dict[str, Any] | None,
) -> CaptureOptions:
    """Build pure capture options from HTTP body and normalized config."""
    cfg = config or {}
    page_metadata_cmd = cfg.get("page_metadata_cmd")
    timeout = cfg.get("page_metadata_timeout_seconds", 5)
    return CaptureOptions(
        dry_run=body_flag(body, "dry_run", default=False),
        force_new=body_flag(body, "force_new", default=False),
        page_metadata_cmd=(
            page_metadata_cmd
            if isinstance(page_metadata_cmd, str) and page_metadata_cmd.strip()
            else None
        ),
        page_metadata_timeout_seconds=int(timeout) if isinstance(timeout, int) else 5,
    )


def _maybe_set_fallback_str(
    body: dict[str, Any],
    body_key: str,
    overrides: dict[str, object],
    record_key: str,
    *,
    overwrite: bool = True,
) -> None:
    """Set a fallback override from a string body field, if valid.

    ``overwrite=False`` yields to a value a higher-precedence source already
    set, rather than replacing it.
    """
    if not overwrite and record_key in overrides:
        return
    value = body.get(body_key)
    if isinstance(value, str) and value.strip():
        overrides[record_key] = value.strip()


def _maybe_validate_authors_str(
    body: dict[str, Any],
    body_key: str,
    overrides: dict[str, object],
    record_key: str,
    *,
    overwrite: bool = True,
) -> None:
    """Convert author list to ' and '-separated string, if all entries are strings."""
    if not overwrite and record_key in overrides:
        return
    raw = body.get(body_key)
    if not isinstance(raw, list) or not raw:
        return
    valid: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            valid.append(item.strip())
        else:
            return  # reject mixed types — safety gate
    if not valid:
        return
    overrides[record_key] = " and ".join(dict.fromkeys(valid))


def metadata_url_override_error(
    body: dict[str, Any],
    *,
    safe_url: Callable[[str], bool],
) -> str | None:
    # Every URL-bearing field the body can set, not a subset. `embedded_pdf_url`
    # was omitted, and it becomes `fallback_pdf_url` on the record — i.e. a
    # private or loopback URL the acquisition planner would then go and fetch.
    for key in ("canonical_url", "source_url", "abstract_url", "embedded_pdf_url"):
        value = body.get(key)
        if isinstance(value, str) and value.strip() and not safe_url(value):
            return f"{key} must be a public http(s) URL"
    return None


def pdf_url_candidates_from_body(
    body: dict[str, Any],
    *,
    safe_url: Callable[[str], bool],
    max_candidates: int = MAX_PDF_URL_CANDIDATES,
) -> list[str] | None | bool:
    raw_candidates = body.get("pdf_url_candidates")
    if not isinstance(raw_candidates, list):
        return None
    if len(raw_candidates) > max_candidates:
        return False
    candidates: list[str] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        stripped = candidate.strip()
        if not safe_url(stripped):
            return False
        candidates.append(stripped)
    return candidates


# ---------------------------------------------------------------------------
# POST route handlers
# ---------------------------------------------------------------------------

JsonResponse = tuple[int, dict[str, Any]]
PostHandler = Callable[[Any, "PostContext"], JsonResponse]


@dataclass(frozen=True)
class PostContext:
    config_path: str
    home_dir: str
    browser_manager: object | None
    attach_session_store: AttachSessionStore | None
    request_id_factory: Callable[[], str]
    token_factory: Callable[[], str]
    now: Callable[[], float]
    max_browser_pdf_bytes: int


@dataclass(frozen=True)
class PostRoute:
    path: str
    handler: PostHandler


def _reject_unconfigured_bib(
    body: Any, config_path: str, home_dir: str
) -> tuple[int, dict[str, Any]] | None:
    """Reject a request naming a library the config does not declare."""
    if not isinstance(body, dict):
        return None
    config_result = load_config_file(config_path, home_dir=home_dir)
    return reject_unconfigured_bib_selector(
        body.get("bib"), config=config_result["config"], home_dir=home_dir
    )


def process_post_request(
    path: str,
    body: Any,
    config_path: str,
    home_dir: str,
    *,
    browser_manager: object | None = None,
    attach_session_store: AttachSessionStore | None = None,
    request_id_factory: Callable[[], str] | None = None,
    token_factory: Callable[[], str] | None = None,
    time_factory: Callable[[], float] | None = None,
    max_browser_pdf_bytes: int = MAX_BROWSER_PDF_BYTES,
) -> tuple[int, dict[str, Any]]:
    """Process a POST body without server/socket dependencies.

    Side effects happen only through service calls and injected adapters, making
    route behavior testable as data in → ``(status, payload)`` out.
    """
    now = time_factory or time.time
    context = PostContext(
        config_path=config_path,
        home_dir=home_dir,
        browser_manager=browser_manager,
        attach_session_store=attach_session_store,
        request_id_factory=request_id_factory or _new_request_id,
        token_factory=token_factory or _new_attach_token,
        now=now,
        max_browser_pdf_bytes=max_browser_pdf_bytes,
    )
    parsed = urlsplit(path)
    p = parsed.path

    # Confine every request to the configured libraries before any handler runs.
    # `bib` accepts a direct .bib path on the CLI as a convenience; honouring
    # that over HTTP would let any request reaching the API create and write a
    # library anywhere the user can write.
    rejection = _reject_unconfigured_bib(body, config_path, home_dir)
    if rejection is not None:
        return rejection

    for route in POST_ROUTES:
        if p == route.path:
            return route.handler(body, context)

    return 404, {"error": "not found"}


def _route_capture(body: Any, context: PostContext) -> JsonResponse:
    return _handle_capture_post(
        body,
        context.config_path,
        context.home_dir,
        attach_session_store=context.attach_session_store,
        request_id_factory=context.request_id_factory,
        token_factory=context.token_factory,
        now=context.now,
    )


def _route_attach_pdf_bytes(body: Any, context: PostContext) -> JsonResponse:
    return _handle_attach_pdf_post(
        body,
        context.config_path,
        context.home_dir,
        attach_session_store=context.attach_session_store,
        now=context.now,
    )


def _route_attach_pdf_raw(body: Any, context: PostContext) -> JsonResponse:
    return _handle_attach_pdf_raw_post(
        body,
        context.config_path,
        context.home_dir,
        attach_session_store=context.attach_session_store,
        now=context.now,
    )


def _route_tags_add(body: Any, context: PostContext) -> JsonResponse:
    return _handle_tags_add_post(body, context.config_path, context.home_dir)


def _route_tags_remove(body: Any, context: PostContext) -> JsonResponse:
    return _handle_tags_remove_post(body, context.config_path, context.home_dir)


def _route_update(body: Any, context: PostContext) -> JsonResponse:
    return _handle_update_post(body, context.config_path, context.home_dir)


def _route_promote(body: Any, context: PostContext) -> JsonResponse:
    return _handle_promote_post(body, context.config_path, context.home_dir)


def _route_browser_discover(body: Any, context: PostContext) -> JsonResponse:
    return _handle_browser_discover_post(body, context.browser_manager)


def _route_browser_download(body: Any, context: PostContext) -> JsonResponse:
    return _handle_browser_download_post(
        body, context.browser_manager, max_pdf_bytes=context.max_browser_pdf_bytes
    )


def _route_delete(body: Any, context: PostContext) -> JsonResponse:
    return _handle_delete_post(body, context.config_path, context.home_dir)


def _route_inbox_drain(body: Any, context: PostContext) -> JsonResponse:
    return _handle_inbox_drain_post(body, context.config_path, context.home_dir)


POST_ROUTES: tuple[PostRoute, ...] = (
    PostRoute("/capture", _route_capture),
    PostRoute("/attach-pdf-bytes", _route_attach_pdf_bytes),
    PostRoute("/attach-pdf-raw", _route_attach_pdf_raw),
    PostRoute("/tags/add", _route_tags_add),
    PostRoute("/tags/remove", _route_tags_remove),
    PostRoute("/update", _route_update),
    PostRoute("/promote", _route_promote),
    PostRoute("/browser/discover", _route_browser_discover),
    PostRoute("/browser/download", _route_browser_download),
    PostRoute("/delete", _route_delete),
    PostRoute("/inbox/drain", _route_inbox_drain),
)


def _handle_browser_discover_post(
    body: Any, browser_manager: object | None,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "body must be a JSON object"}
    if browser_manager is None:
        return 503, {"error": "browser session not available"}
    page_url = body.get("page_url")
    if not isinstance(page_url, str) or not page_url.strip():
        return 400, {"error": "page_url required"}
    normalized_page_url = page_url.strip()
    if not safe_public_http_url(normalized_page_url):
        return 400, {"error": "page_url must be a public http(s) URL"}
    discover = getattr(browser_manager, "discover_pdf_url", None)
    if not callable(discover):
        return 503, {"error": "browser session not available"}
    # No `doi=`: the manager accepted one and dropped it on the floor, because
    # `browser_pdf_hook.discover_pdf_url` has no parameter to forward it to.
    # Sending it implied a hint was being used that never was.
    pdf_url = discover(normalized_page_url)
    if pdf_url:
        return 200, {"pdf_url": pdf_url}
    return 200, {"pdf_url": None}


def _handle_browser_download_post(
    body: Any, browser_manager: object | None, *, max_pdf_bytes: int = MAX_BROWSER_PDF_BYTES,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "body must be a JSON object"}
    if browser_manager is None:
        return 503, {"error": "browser session not available"}
    pdf_url = body.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip():
        return 400, {"error": "pdf_url required"}
    normalized_pdf_url = pdf_url.strip()
    if not safe_public_http_url(normalized_pdf_url):
        return 400, {"error": "pdf_url must be a public http(s) URL"}
    download = getattr(browser_manager, "download_pdf_bytes", None)
    if not callable(download):
        return 503, {"error": "browser session not available"}
    pdf_bytes = cast("bytes | None", download(normalized_pdf_url))
    if pdf_bytes:
        if len(pdf_bytes) > max(0, max_pdf_bytes):
            return 413, {"error": "PDF too large"}
        return 200, {"pdf_base64": base64.b64encode(pdf_bytes).decode()}
    return 200, {"pdf_base64": None}


def _handle_delete_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "body must be a JSON object"}
    citekey = body.get("citekey")
    if not isinstance(citekey, str) or not citekey.strip():
        return 400, {"error": "citekey required"}
    bib_selector = body.get("bib") if isinstance(body.get("bib"), str) else None
    force = body.get("force") is True
    raw_dry_run = body.get("dry_run")
    if raw_dry_run is False and not force:
        return 400, {"error": "force=true required for destructive delete"}
    # `force` decides the *default*, never overrides an explicit request: asking
    # for `dry_run: true` and getting a real delete is the one outcome this
    # endpoint must never produce. Without force the default stays a preview;
    # with it, the default is the delete the caller came for.
    dry_run = body_flag(body, "dry_run", default=not force)

    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, BibResolutionFailure):
        return 400, {"status": "error", "errors": resolved.errors}

    _config, bib = resolved
    result = delete_entry(
        bib_path=bib["path"],
        citekey=citekey.strip(),
        dry_run=dry_run,
    )
    status = status_for_service_result(result)
    return status, dict(result)


def _confined_local_capture_path(
    value: str, *, config_path: str, home_dir: str
) -> tuple[str | None, str | None]:
    """Confine a local capture path to `capture_source_dirs`.

    Returns ``(resolved_path, error)`` — exactly one of which is set. The
    *resolved* path is handed back because that is the one the check was made
    about: passing the caller's original string on meant the containment
    decision and the file actually opened were two different paths, and the
    difference between them is precisely what symlinks and ``..`` control.
    """
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg.get("config")
    roots = tuple(config.get("capture_source_dirs") or ()) if config else ()
    if not roots:
        return None, (
            "local file capture is not enabled over HTTP; "
            "set capture_source_dirs in config to allow it"
        )
    confined = path_confined_to(value, roots)
    if confined is None:
        return None, "path is outside the configured capture_source_dirs"
    return str(confined), None


def _handle_capture_post(
    body: Any,
    config_path: str,
    home_dir: str,
    *,
    attach_session_store: AttachSessionStore | None,
    request_id_factory: Callable[[], str],
    token_factory: Callable[[], str],
    now: Callable[[], float],
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "capture body must be a JSON object"}
    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        return 400, {"error": "url required"}
    stripped_url = url.strip()
    parsed_url = urlsplit(stripped_url)
    if parsed_url.scheme:
        if not safe_public_http_url(stripped_url):
            return 400, {"error": "url must be a public http(s) URL for HTTP capture"}
    else:
        # No scheme means this is a local filesystem path, and the SSRF guard
        # above does not apply to it. Left unchecked, `add_local_pdf` reads the
        # file (sending extracted text to metadata providers) and copies it into
        # `papers_dir`, from where `GET /pdf/<citekey>` serves it — laundering
        # around the read-side confinement. The extension only ever sends
        # http(s) URLs, so this path is opt-in: with no `capture_source_dirs`
        # configured the allowlist is empty and every local path is refused.
        confined_path, capture_error = _confined_local_capture_path(
            stripped_url, config_path=config_path, home_dir=home_dir,
        )
        if capture_error is not None or confined_path is None:
            return 400, {"error": capture_error or "path is not allowed"}
        stripped_url = confined_path
    override_error = metadata_url_override_error(body, safe_url=safe_public_http_url)
    if override_error is not None:
        return 400, {"error": override_error}
    pdf_candidates = pdf_url_candidates_from_body(
        body,
        safe_url=safe_public_http_url,
    )
    if pdf_candidates is False:
        return 400, {
            "error": (
                "pdf_url_candidates must be public http(s) URLs; send at most 20 "
                "candidates and avoid localhost/private hosts, invalid URLs, or slow DNS names"
            )
        }
    safe_pdf_candidates = pdf_candidates if isinstance(pdf_candidates, list) else None
    browser = body.get("browser") if isinstance(body.get("browser"), str) else None
    service_kwargs: dict[str, Any] = {}
    if browser:
        service_kwargs["browser"] = browser
    config_result = load_config_file(config_path, home_dir=home_dir)
    config = config_result["config"] if config_result["config"] is not None else None
    try:
        result = capture_to_bib(
            capture_input_from_http_body(
                body,
                pdf_candidates=safe_pdf_candidates,
                value_override=stripped_url,
            ),
            capture_options_from_http_body(body, config=config),
            config_path=config_path,
            home_dir=home_dir,
            service_kwargs=service_kwargs,
        )
    except ConcurrentEditError:
        return 409, capture_payload(
            error_result(
                message=CONCURRENT_EDIT_MESSAGE,
                errors=[CONCURRENT_EDIT_MESSAGE],
                dry_run=body_flag(body, "dry_run", default=False),
                warnings=[],
            )
        )
    if attach_session_store is not None:
        _maybe_add_pdf_request(
            cast(dict[str, Any], result),
            body=body,
            safe_pdf_candidates=safe_pdf_candidates,
            attach_base_url=_attach_base_url_from_config(config),
            attach_session_store=attach_session_store,
            request_id_factory=request_id_factory,
            token_factory=token_factory,
            now=now,
        )
    status = status_for_service_result(cast(dict[str, Any], result))
    return status, capture_payload(
        cast(dict[str, Any], result), include_diagnostics=body_flag(body, "verbose", default=False)
    )


def _maybe_add_pdf_request(
    result: dict[str, Any],
    *,
    body: dict[str, Any],
    safe_pdf_candidates: list[str] | None,
    attach_base_url: str,
    attach_session_store: AttachSessionStore,
    request_id_factory: Callable[[], str],
    token_factory: Callable[[], str],
    now: Callable[[], float],
) -> None:
    if result.get("status") != "ok" or result.get("pdf_path"):
        return
    citekey = result.get("citekey")
    bib = result.get("bib_name")
    page_url = body.get("url")
    if not isinstance(citekey, str) or not citekey.strip():
        return
    if not isinstance(page_url, str) or not page_url.strip():
        return
    candidates = list(safe_pdf_candidates or [])
    pdf_url = result.get("pdf_url")
    if isinstance(pdf_url, str) and pdf_url.strip():
        candidates.append(pdf_url.strip())
    request_id = request_id_factory()
    token = token_factory()
    plan = build_pdf_acquisition_plan(
        citekey=citekey.strip(),
        bib=bib if isinstance(bib, str) else None,
        page_url=page_url.strip(),
        pdf_urls=candidates,
        attach_base_url=attach_base_url,
        request_id=request_id,
        attach_token=token,
    )
    if plan is None:
        return
    session = build_attach_session(
        request_id=request_id,
        token=token,
        citekey=citekey.strip(),
        bib=bib if isinstance(bib, str) else None,
        created_at=now(),
        ttl_seconds=ATTACH_SESSION_TTL_SECONDS,
        max_bytes=MAX_BROWSER_PDF_BYTES,
        allowed_source_urls=[str(c["url"]) for c in cast(list[dict[str, str]], plan["candidates"])],
    )
    attach_session_store.put(session)
    result["pdf_request"] = plan


def _attach_base_url_from_config(config: Mapping[str, Any] | None) -> str:
    api_url = config.get("api_url") if isinstance(config, Mapping) else None
    base = api_url.strip().rstrip("/") if isinstance(api_url, str) and api_url.strip() else "http://127.0.0.1:8765"
    return f"{base}/attach-pdf-raw"


def _handle_attach_pdf_post(
    body: Any,
    config_path: str,
    home_dir: str,
    *,
    attach_session_store: AttachSessionStore | None,
    now: Callable[[], float],
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "attach body must be a JSON object"}
    citekey = body.get("citekey")
    pdf_base64 = body.get("pdf_base64")
    if not isinstance(citekey, str) or not citekey.strip():
        return 400, {"error": "citekey required"}
    if not isinstance(pdf_base64, str) or not pdf_base64.strip():
        return 400, {"error": "pdf_base64 required"}
    source_url = body.get("source_url") if isinstance(body.get("source_url"), str) else None
    if source_url is not None and not safe_public_http_url(source_url):
        return 400, {"error": "source_url must be a public http(s) URL"}
    request_id = body.get("request_id") if isinstance(body.get("request_id"), str) else None
    session = None
    if request_id is not None:
        if attach_session_store is None:
            return 403, {"error": "attach session store unavailable"}
        session = attach_session_store.claim(request_id)
        if session is None:
            return 403, {"error": "attach session not found"}
        try:
            pdf_bytes = base64.b64decode(pdf_base64, validate=True)
        except (ValueError, binascii.Error):
            attach_session_store.restore(session)
            return 400, {"error": "pdf_base64 invalid"}
        attach_token_value = body.get("attach_token")
        token: str = attach_token_value if isinstance(attach_token_value, str) else ""
        validation_error = validate_attach_request(
            session,
            request_id=request_id,
            token=token,
            citekey=citekey,
            bib=body.get("bib") if isinstance(body.get("bib"), str) else None,
            pdf_bytes=pdf_bytes,
            source_url=source_url,
            origin_candidate=_origin_candidate(body),
            now=now(),
        )
        if validation_error is not None:
            attach_session_store.restore(session)
            return 403, {"error": validation_error}
    else:
        # The sessionless upload is deliberate and documented — a capture that
        # never opened a session still has to be able to attach — but arriving
        # without a session is not a reason to accept an unbounded PDF. The
        # session path checks exactly this via `session.max_bytes`; the same
        # cap applies here.
        try:
            decoded_size = len(base64.b64decode(pdf_base64, validate=True))
        except (ValueError, binascii.Error):
            return 400, {"error": "pdf_base64 invalid"}
        if decoded_size > MAX_BROWSER_PDF_BYTES:
            return 413, {
                "error": f"pdf too large: {decoded_size} > {MAX_BROWSER_PDF_BYTES} bytes"
            }
    result = attach_pdf_bytes(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        pdf_base64=pdf_base64,
        source_url=source_url,
    )
    status = status_for_service_result(result)
    if status != 200 and session is not None and attach_session_store is not None:
        attach_session_store.restore(session)
    return status, result


def _origin_candidate(body: Any) -> str | None:
    """The planned candidate the caller began from, if it named one.

    Acquisition legitimately leaves the plan — a publisher redirect to a CDN is
    the normal case — so the observed `source_url` is often on another host.
    The plan still authorises; this says which planned candidate the fetch
    started from, and `source_url` records where the bytes came from.
    """
    if not isinstance(body, dict):
        return None
    value = body.get("origin_candidate")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _handle_attach_pdf_raw_post(
    body: Any,
    config_path: str,
    home_dir: str,
    *,
    attach_session_store: AttachSessionStore | None,
    now: Callable[[], float],
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "attach body must be a JSON object"}
    citekey = body.get("citekey")
    pdf_bytes = body.get("pdf_bytes")
    if not isinstance(citekey, str) or not citekey.strip():
        return 400, {"error": "citekey required"}
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes.startswith(b"%PDF-"):
        return 400, {"error": "pdf_bytes must start with %PDF-"}
    source_url = body.get("source_url") if isinstance(body.get("source_url"), str) else None
    if source_url is not None and not safe_public_http_url(source_url):
        return 400, {"error": "source_url must be a public http(s) URL"}
    request_id = body.get("request_id") if isinstance(body.get("request_id"), str) else None
    if request_id is None:
        # `docs/security.md` presents the attach-session checks — TTL, byte
        # limit, allowlisted source URL, citekey and bib — as *the* control on
        # this route. They only ran when the caller volunteered a `request_id`,
        # so omitting it skipped every one of them and wrote bytes under any
        # citekey. A control the caller can decline is not a control.
        return 403, {"error": "request_id required: attach must reference a capture"}
    if attach_session_store is None:
        return 403, {"error": "attach session store unavailable"}
    session = attach_session_store.claim(request_id)
    if session is None:
        return 403, {"error": "attach session not found"}
    attach_token_value = body.get("attach_token")
    token: str = attach_token_value if isinstance(attach_token_value, str) else ""
    validation_error = validate_attach_request(
        session,
        request_id=request_id,
        token=token,
        citekey=citekey,
        bib=body.get("bib") if isinstance(body.get("bib"), str) else None,
        pdf_bytes=pdf_bytes,
        source_url=source_url,
        origin_candidate=_origin_candidate(body),
        now=now(),
    )
    if validation_error is not None:
        attach_session_store.restore(session)
        return 403, {"error": validation_error}
    result = attach_pdf_raw_bytes(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        pdf_bytes=pdf_bytes,
        source_url=source_url,
    )
    status = status_for_service_result(result)
    if status != 200 and session is not None and attach_session_store is not None:
        attach_session_store.restore(session)
    return status, result


def _handle_tags_add_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "tags body must be a JSON object"}
    citekey = body.get("citekey")
    tags = body.get("tags")
    if not isinstance(citekey, str) or not citekey.strip():
        return 400, {"error": "citekey required"}
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return 400, {"error": "tags must be a list of strings"}
    result = add_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        tags=tags,
        dry_run=body_flag(body, "dry_run", default=False),
    )
    status = status_for_service_result(result)
    return status, tag_change_payload(result)


def _handle_tags_remove_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "tags body must be a JSON object"}
    citekey = body.get("citekey")
    tags = body.get("tags")
    if not isinstance(citekey, str) or not citekey.strip():
        return 400, {"error": "citekey required"}
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return 400, {"error": "tags must be a list of strings"}
    result = remove_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        tags=tags,
        dry_run=body_flag(body, "dry_run", default=False),
    )
    status = status_for_service_result(result)
    return status, tag_change_payload(result)


def _handle_update_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "update body must be a JSON object"}
    try:
        result = update_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
            dry_run=body_flag(body, "dry_run", default=True),
        )
    except ConcurrentEditError:
        return 409, {"error": CONCURRENT_EDIT_MESSAGE}
    status = status_for_service_result(result)
    return status, update_payload(
        result, include_diagnostics=body_flag(body, "verbose", default=False)
    )


def _handle_promote_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "promote body must be a JSON object"}
    try:
        result = promote_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
            keep_preprint=not body_flag(body, "replace", default=False),
            dry_run=body_flag(body, "dry_run", default=True),
        )
    except ConcurrentEditError:
        return 409, {"error": CONCURRENT_EDIT_MESSAGE}
    status = status_for_service_result(result)
    return status, promote_payload(
        result, include_diagnostics=body_flag(body, "verbose", default=False)
    )


def _handle_inbox_drain_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "inbox body must be a JSON object"}
    inbox_path = body.get("file")
    if not isinstance(inbox_path, str) or not inbox_path.strip():
        return 400, {"error": "inbox body must include a 'file' path string"}
    # Draining *rewrites* the named file in place — it also creates a `.lock`
    # beside it and any missing parent directories — so an unvalidated `file`
    # let any loopback-reachable client truncate a file the user can write.
    # Only the configured inbox may be drained; with none configured the route
    # is closed, which is the safe reading of "not set up".
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg.get("config")
    configured_inbox = config.get("inbox_path") if config else None
    if not configured_inbox:
        return 400, {
            "error": "inbox draining is not enabled over HTTP; set inbox_path in config"
        }
    confined_inbox = path_confined_to(inbox_path.strip(), [configured_inbox])
    if confined_inbox is None:
        return 400, {"error": "file must be the configured inbox_path"}

    raw_delay = body.get("delay")
    # `bool` is an `int` subclass, so `{"delay": true}` would otherwise become
    # 1.0. Bad input used to coerce silently to 0.0, and the HTTP default was
    # 0.0 while the CLI default is 1.0 — so this route hammered metadata
    # providers with no politeness delay. Match the CLI and cap it, since an
    # unbounded sleep pins a server thread.
    if raw_delay is None:
        delay = _DEFAULT_INBOX_DELAY_SECONDS
    elif isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float)):
        return 400, {"error": "delay must be a number"}
    elif raw_delay < 0:
        return 400, {"error": "delay must not be negative"}
    else:
        delay = min(float(raw_delay), _MAX_INBOX_DELAY_SECONDS)

    from pzi.inbox_service import drain_inbox
    raw_tags = body.get("tags")
    extra_tags = [t for t in raw_tags if isinstance(t, str)] if isinstance(raw_tags, list) else None
    result = drain_inbox(
        config_path=config_path,
        home_dir=home_dir,
        # The resolved path, not the caller's spelling of it — see
        # `_confined_local_capture_path`.
        inbox_path=str(confined_inbox),
        dry_run=body_flag(body, "dry_run", default=False),
        extra_tags=extra_tags,
        delay=delay,
    )
    status = status_for_service_result(result)
    return status, inbox_drain_payload(result)


# ---------------------------------------------------------------------------
# ID / token factories
# ---------------------------------------------------------------------------


def _new_request_id() -> str:
    return secrets.token_urlsafe(18)


def _new_attach_token() -> str:
    return secrets.token_urlsafe(32)
