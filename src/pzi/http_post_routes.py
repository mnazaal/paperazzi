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

from pzi.bib_service import delete_entry
from pzi.bibtex import normalize_authors
from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput, CaptureOptions, PageArtifact, PdfCandidate
from pzi.config import load_and_resolve_bib, load_config_file
from pzi.http_payloads import capture_payload, promote_payload, tag_change_payload, update_payload
from pzi.http_security import DEFAULT_MAX_BODY_BYTES, safe_public_http_url
from pzi.http_status import status_for_service_result
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
ATTACH_SESSION_TTL_SECONDS = 600
MAX_BROWSER_PDF_BYTES = DEFAULT_MAX_BODY_BYTES

# ---------------------------------------------------------------------------
# Capture body helpers
# ---------------------------------------------------------------------------


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
    # JSON-LD / OG fallbacks — used when citation_* meta is absent
    # Order: OG first, then JSON-LD (JSON-LD is more reliable, wins if both present)
    _maybe_set_fallback_str(body, "embedded_og_title", record_overrides, "fallback_title")
    _maybe_validate_authors_str(
        body, "embedded_jsonld_authors", record_overrides, "fallback_authors"
    )
    _maybe_set_fallback_str(body, "embedded_jsonld_title", record_overrides, "fallback_title")
    _maybe_set_fallback_str(body, "embedded_jsonld_year", record_overrides, "fallback_year")
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
) -> CaptureInput:
    """Build pure capture input from validated HTTP capture body."""
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
        value=str(body["url"]).strip(),
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
        dry_run=bool(body.get("dry_run", False)),
        force_new=bool(body.get("force_new", False)),
        page_metadata_cmd=(
            page_metadata_cmd
            if isinstance(page_metadata_cmd, str) and page_metadata_cmd.strip()
            else None
        ),
        page_metadata_timeout_seconds=int(timeout) if isinstance(timeout, int) else 5,
    )


def _maybe_set_fallback_str(
    body: dict[str, Any], body_key: str, overrides: dict[str, object], record_key: str
) -> None:
    """Set a fallback override from a string body field, if valid."""
    value = body.get(body_key)
    if isinstance(value, str) and value.strip():
        overrides[record_key] = value.strip()


def _maybe_validate_authors_str(
    body: dict[str, Any], body_key: str, overrides: dict[str, object], record_key: str
) -> None:
    """Convert author list to ' and '-separated string, if all entries are strings."""
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
    for key in ("canonical_url", "source_url", "abstract_url"):
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
    doi = body.get("doi") if isinstance(body.get("doi"), str) else None
    discover = getattr(browser_manager, "discover_pdf_url", None)
    if not callable(discover):
        return 503, {"error": "browser session not available"}
    pdf_url = discover(normalized_page_url, doi=doi)
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
    dry_run = False if force else bool(body.get("dry_run", True))

    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}

    _config, bib = resolved
    result = delete_entry(
        bib_path=bib["path"],
        citekey=citekey.strip(),
        dry_run=dry_run,
    )
    status = status_for_service_result(result)
    return status, result


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
    if parsed_url.scheme and not safe_public_http_url(stripped_url):
        return 400, {"error": "url must be a public http(s) URL for HTTP capture"}
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
    result = capture_to_bib(
        capture_input_from_http_body(body, pdf_candidates=safe_pdf_candidates),
        capture_options_from_http_body(body, config=config),
        config_path=config_path,
        home_dir=home_dir,
        service_kwargs=service_kwargs,
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
        cast(dict[str, Any], result), include_diagnostics=bool(body.get("verbose", False))
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
    if request_id is not None:
        if attach_session_store is None:
            return 403, {"error": "attach session store unavailable"}
        session = attach_session_store.get(request_id)
        if session is None:
            return 403, {"error": "attach session not found"}
        try:
            pdf_bytes = base64.b64decode(pdf_base64, validate=True)
        except (ValueError, binascii.Error):
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
            now=now(),
        )
        if validation_error is not None:
            return 403, {"error": validation_error}
    result = attach_pdf_bytes(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        pdf_base64=pdf_base64,
        source_url=source_url,
    )
    status = status_for_service_result(result)
    if status == 200 and request_id is not None and attach_session_store is not None:
        attach_session_store.consume(request_id)
    return status, result


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
    if request_id is not None:
        if attach_session_store is None:
            return 403, {"error": "attach session store unavailable"}
        session = attach_session_store.get(request_id)
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
            now=now(),
        )
        if validation_error is not None:
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
    if status == 200 and request_id is not None and attach_session_store is not None:
        attach_session_store.consume(request_id)
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
        dry_run=bool(body.get("dry_run", False)),
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
        dry_run=bool(body.get("dry_run", False)),
    )
    status = status_for_service_result(result)
    return status, tag_change_payload(result)


def _handle_update_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "update body must be a JSON object"}
    result = update_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        dry_run=bool(body.get("dry_run", True)),
    )
    status = status_for_service_result(result)
    return status, update_payload(
        result, include_diagnostics=bool(body.get("verbose", False))
    )


def _handle_promote_post(
    body: Any, config_path: str, home_dir: str,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "promote body must be a JSON object"}
    result = promote_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        keep_preprint=not bool(body.get("replace", False)),
        dry_run=bool(body.get("dry_run", True)),
    )
    status = status_for_service_result(result)
    return status, promote_payload(
        result, include_diagnostics=bool(body.get("verbose", False))
    )


# ---------------------------------------------------------------------------
# ID / token factories
# ---------------------------------------------------------------------------


def _new_request_id() -> str:
    return secrets.token_urlsafe(18)


def _new_attach_token() -> str:
    return secrets.token_urlsafe(32)
