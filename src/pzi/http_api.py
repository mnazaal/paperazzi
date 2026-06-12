"""Local HTTP capture API backed by the same service pipeline as the CLI."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pzi.bib_repository import read_bib_file
from pzi.bib_service import delete_entry, list_bibs
from pzi.bibtex import normalize_authors
from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput, CaptureOptions, PageArtifact, PdfCandidate
from pzi.config import load_and_resolve_bib, load_config_file
from pzi.doctor_service import doctor_check
from pzi.http_security import (
    AUTH_HEADER,
    HttpSecurityConfig,
    RateLimiter,
    build_http_security_config,
    origin_allowed,
    request_security_error,
    safe_public_http_url,
    validated_content_length,
)
from pzi.pdf_acquisition_plan import build_pdf_acquisition_plan
from pzi.pdf_attach_session import build_attach_session, validate_attach_request
from pzi.pdf_attach_session_store import AttachSessionStore
from pzi.pdf_service import attach_pdf_bytes, attach_pdf_raw_bytes
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib
from pzi.tag_service import add_tags, list_tags, remove_tags
from pzi.update_service import update_bib

# ---------------------------------------------------------------------------
# Payload helpers (merged from http_payloads.py)
# ---------------------------------------------------------------------------

MAX_PDF_URL_CANDIDATES = 20
ATTACH_SESSION_TTL_SECONDS = 600
MAX_BROWSER_PDF_BYTES = 75_000_000


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
    # Normal overrides overwrite translation-server output instead of filling blanks only.
    trusted_fields = body.get("trusted_fields")
    if isinstance(trusted_fields, list):
        for field in trusted_fields:
            if not isinstance(field, str) or not field.strip():
                continue
            name = field.strip()
            fallback_key = f"fallback_{name}"
            if fallback_key in record_overrides:
                value = record_overrides.pop(fallback_key)
                # Normalize authors back to list to match Zotero's internal format.
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
            page_metadata_cmd if isinstance(page_metadata_cmd, str) and page_metadata_cmd.strip() else None
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


def capture_payload(
    result: dict[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    payload = {
        "status": result["status"],
        "bib": result["bib_name"],
        "citekey": result["citekey"],
        "action": result["action"],
        "pdf_path": result["pdf_path"],
        "pdf_url": result.get("pdf_url"),
        "pdf_status": result.get(
            "pdf_status",
            "direct_saved" if result.get("pdf_path") else "none",
        ),
        "pdf_error": result.get("pdf_error"),
        "pdf_suggestion": result.get("pdf_suggestion"),
        "dry_run": result["dry_run"],
        "message": result["message"],
        "warnings": result["warnings"],
        "errors": result["errors"],
        "changed_fields": result.get("changed_fields", []),
    }
    if include_diagnostics and result.get("metadata_diagnostics"):
        payload["metadata_diagnostics"] = result["metadata_diagnostics"]
    if result.get("pdf_request"):
        payload["pdf_request"] = result["pdf_request"]
    return payload


def _base_payload(result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Common payload skeleton: status, bib, errors."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "errors": result.get("errors", []),
        **extra,
    }


def search_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a search_bib result for the HTTP API."""
    matches = result.get("matches", [])
    return _base_payload(result, matches=matches, total=len(matches))


def entries_payload(result: dict[str, Any], offset: int, limit: int) -> dict[str, Any]:
    """Serialize a search_bib result as a paginated entries list."""
    matches = result.get("matches", [])
    return _base_payload(
        result,
        entries=matches[offset : offset + limit],
        total=len(matches),
        offset=offset,
        limit=limit,
    )


def detail_payload(record: dict[str, Any], bib_name: str | None) -> dict[str, Any]:
    """Serialize a single BibTeX record for the HTTP API."""
    tags = list(record.get("tags") or [])
    return {
        "status": "ok",
        "bib": bib_name,
        "citekey": record.get("citekey"),
        "entry": {
            "citekey": record.get("citekey"),
            "title": record.get("title"),
            "authors": normalize_authors(record.get("authors")),
            "year": record.get("year"),
            "doi": record.get("doi"),
            "url": record.get("url"),
            "venue": record.get("venue"),
            "abstract": record.get("abstract"),
            "note": record.get("note"),
            "tags": sorted(tags),
            "local_pdf_path": record.get("local_pdf_path"),
            "pdf_url": record.get("pdf_url"),
        },
        "errors": [],
    }


def tag_list_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a list_tags result for the HTTP API."""
    return _base_payload(
        result,
        citekey=result.get("citekey"),
        tags=result.get("tags", []),
    )


def tag_change_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a tag add/remove result for the HTTP API."""
    return _base_payload(
        result,
        citekey=result.get("citekey"),
        tags=result.get("tags", []),
        changed=result.get("changed", False),
        dry_run=result.get("dry_run", False),
        message=result.get("message", ""),
    )


def update_payload(
    result: dict[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Serialize an update_bib result for the HTTP API."""
    return _base_payload(
        result,
        dry_run=result.get("dry_run", True),
        items=_items_payload(
            result.get("items", []), include_diagnostics=include_diagnostics
        ),
    )


def promote_payload(
    result: dict[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Serialize a promote_bib result for the HTTP API."""
    return _base_payload(
        result,
        dry_run=result.get("dry_run", True),
        keep_preprint=result.get("keep_preprint", True),
        items=_items_payload(
            result.get("items", []), include_diagnostics=include_diagnostics
        ),
        summary=result.get("summary", {}),
    )


def _items_payload(items: object, *, include_diagnostics: bool) -> list[Any]:
    if not isinstance(items, list):
        return []
    if include_diagnostics:
        return items
    filtered: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        clean = dict(item)
        clean.pop("metadata_diagnostics", None)
        filtered.append(clean)
    return filtered


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def build_handler_class(
    *,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig | None = None,
    browser_manager: object | None = None,
    attach_session_store: AttachSessionStore | None = None,
) -> type[BaseHTTPRequestHandler]:
    security_config = security or build_http_security_config()
    store = attach_session_store or AttachSessionStore()
    return type(
        "PziHandler",
        (BaseHTTPRequestHandler,),
        {
            "server_version": "pzi/0.1",
            "_rate_limiter": RateLimiter(max_requests=security_config["rate_limit_rpm"]),
            "_browser_session_manager": browser_manager,
            "_attach_session_store": store,
            "do_OPTIONS": lambda request: _handle_options(request, security_config),
            "do_GET": lambda request: _handle_get(request, config_path, home_dir, security_config),
            "do_POST": lambda request: _handle_post(
                request, config_path, home_dir, security_config
            ),
            "log_message": lambda request, format, *args: None,  # noqa: A002
        },
    )


def _handle_options(request: BaseHTTPRequestHandler, security: HttpSecurityConfig) -> None:
    error = request_security_error(
        method="OPTIONS", headers=dict(request.headers.items()), security=security
    )
    if error is not None:
        _respond(request, error[0], {"error": error[1]}, security)
        return
    request.send_response(204)
    _send_cors_headers(request, security)
    request.end_headers()


def process_get_request(
    path: str,
    config_path: str,
    home_dir: str,
) -> tuple[int, dict[str, Any]]:
    """Pure: process a GET request path, returning (status, body_dict).

    Testable without a server socket. Used by _handle_get.
    """
    parsed = urlsplit(path)
    p = parsed.path
    qs = _parse_query(parsed.query)

    if p == "/health":
        return 200, _health_payload(config_path, home_dir)
    if p == "/bibs":
        result = list_bibs(config_path=config_path, home_dir=home_dir)
        status = 200 if result["status"] == "ok" else 500
        return status, result
    if p == "/search":
        return _handle_search_get(config_path, home_dir, qs)
    if p == "/entries":
        return _handle_entries_get(config_path, home_dir, qs)
    if p.startswith("/detail/"):
        citekey = p[len("/detail/"):]
        bib_selector = qs.get("bib")
        return _handle_detail_get(config_path, home_dir, citekey, bib_selector)
    if p.startswith("/tags/"):
        citekey = p[len("/tags/"):]
        bib_selector = qs.get("bib")
        return _handle_tags_get(config_path, home_dir, citekey, bib_selector)
    if p == "/export":
        return _handle_export_get(config_path, home_dir, qs)
    return 404, {"error": "not found"}


def _parse_query(query_string: str) -> dict[str, str]:
    """Parse a URL query string into a flat dict of single-value params."""
    result: dict[str, str] = {}
    for key, values in parse_qs(query_string).items():
        if values:
            result[key] = values[0]
    return result


def _handle_search_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    bib_selector = qs.get("bib") or None
    query = qs.get("q") or None
    author = qs.get("author") or None
    year_raw = qs.get("year")
    year: int | None = None
    if year_raw is not None:
        try:
            year = int(year_raw)
        except ValueError:
            return 400, {"error": "year must be an integer"}
    tag = qs.get("tag") or None

    result = search_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        query=query,
        author=author,
        year=year,
        tag=tag,
    )
    status = 200 if result["status"] == "ok" else 400
    return status, search_payload(result)


def _handle_entries_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    bib_selector = qs.get("bib") or None
    offset = _parse_int(qs.get("offset"), 0)
    limit = _parse_int(qs.get("limit"), 50)
    limit = max(1, min(limit, 500))

    result = search_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    status = 200 if result["status"] == "ok" else 400
    return status, entries_payload(result, offset, limit)


def _handle_detail_get(
    config_path: str, home_dir: str, citekey: str, bib_selector: str | None,
) -> tuple[int, dict[str, Any]]:
    if not citekey:
        return 400, {"error": "citekey required"}

    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}
    _config, bib = resolved

    read_result = read_bib_file(bib["path"])
    for record in read_result["records"]:
        if record.get("citekey") == citekey:
            return 200, detail_payload(record, bib["name"])
    return 404, {"error": f"citekey not found: {citekey}"}


def _handle_tags_get(
    config_path: str, home_dir: str, citekey: str, bib_selector: str | None,
) -> tuple[int, dict[str, Any]]:
    if not citekey:
        return 400, {"error": "citekey required"}

    result = list_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
    )
    status = 200 if result["status"] == "ok" else 400
    return status, tag_list_payload(result)


def _handle_export_get(
    config_path: str, home_dir: str, qs: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    fmt = qs.get("format", "bibtex")
    if fmt not in ("bibtex", "csv", "json", "ris"):
        return 400, {"error": f"unsupported format: {fmt}"}

    bib_selector = qs.get("bib") or None
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}

    _config, bib = resolved
    from pzi.export_service import export_bibtex, export_csv, export_json, export_ris

    exporters = {
        "bibtex": export_bibtex,
        "csv": export_csv,
        "json": export_json,
        "ris": export_ris,
    }
    result = exporters[fmt](bib_path=bib["path"])

    if result["status"] != "ok":
        return 500, {"error": "export failed", "errors": result.get("errors", [])}

    return 200, {
        "format": result["format"],
        "total_entries": result["total_entries"],
        "content_type": result["content_type"],
        "content": result["content"],
    }


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _serve_pdf(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    citekey: str,
    bib_selector: str | None,
) -> None:
    """Serve a PDF file for a citekey."""
    if not citekey:
        request.send_response(400)
        request.end_headers()
        return

    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        request.send_response(400)
        request.end_headers()
        return

    _config, bib = resolved
    read_result = read_bib_file(bib["path"])
    pdf_path = None
    for record in read_result["records"]:
        if record.get("citekey") == citekey:
            pdf_path = record.get("local_pdf_path")
            break

    if not pdf_path or not os.path.exists(str(pdf_path)):
        request.send_response(404)
        request.end_headers()
        return

    try:
        data = Path(str(pdf_path)).read_bytes()
    except OSError:
        request.send_response(500)
        request.end_headers()
        return

    request.send_response(200)
    request.send_header("Content-Type", "application/pdf")
    request.send_header("Content-Length", str(len(data)))
    request.send_header(
        "Content-Disposition",
        f'inline; filename="{citekey}.pdf"',
    )
    request.end_headers()
    request.wfile.write(data)


def _handle_get(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
) -> None:
    error = request_security_error(
        method="GET", headers=dict(request.headers.items()), security=security
    )
    if error is not None:
        _respond(request, error[0], {"error": error[1]}, security)
        return
    client_id = request.client_address[0] if request.client_address else "unknown"
    allowed, remaining, reset = request._rate_limiter.check(client_id)  # type: ignore[attr-defined]
    if not allowed:
        _respond(request, 429, {"error": "rate limit exceeded"}, security,
                 rate_remaining=0, rate_reset=reset)
        return
    idle_state = getattr(request, "_idle_state", None)
    if idle_state is not None:
        idle_state["_last_request"] = time.monotonic()

    p = urlsplit(request.path).path
    if p.startswith("/pdf/"):
        citekey = p[len("/pdf/"):]
        qs = _parse_query(urlsplit(request.path).query)
        bib = qs.get("bib") or None
        _serve_pdf(request, config_path, home_dir, citekey, bib)
        return

    status, body = process_get_request(request.path, config_path, home_dir)
    _respond(request, status, body, security,
             rate_remaining=remaining, rate_reset=reset)


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
) -> tuple[int, dict[str, Any]]:
    """Pure: process a POST request body, returning (status, body_dict).

    Testable without a server socket. Used by _handle_post.
    """
    parsed = urlsplit(path)
    p = parsed.path
    now = time_factory or time.time

    if p == "/capture":
        return _handle_capture_post(
            body,
            config_path,
            home_dir,
            attach_session_store=attach_session_store,
            request_id_factory=request_id_factory or _new_request_id,
            token_factory=token_factory or _new_attach_token,
            now=now,
        )

    if p == "/attach-pdf-bytes":
        return _handle_attach_pdf_post(body, config_path, home_dir)

    if p == "/attach-pdf-raw":
        return _handle_attach_pdf_raw_post(
            body,
            config_path,
            home_dir,
            attach_session_store=attach_session_store,
            now=now,
        )

    if p == "/tags/add":
        return _handle_tags_add_post(body, config_path, home_dir)

    if p == "/tags/remove":
        return _handle_tags_remove_post(body, config_path, home_dir)

    if p == "/update":
        return _handle_update_post(body, config_path, home_dir)

    if p == "/promote":
        return _handle_promote_post(body, config_path, home_dir)

    if p == "/browser/discover":
        return _handle_browser_discover_post(body, browser_manager)

    if p == "/browser/download":
        return _handle_browser_download_post(body, browser_manager)

    if p == "/delete":
        return _handle_delete_post(body, config_path, home_dir)

    return 404, {"error": "not found"}


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
    doi = body.get("doi") if isinstance(body.get("doi"), str) else None
    from pzi.browser_session_manager import BrowserSessionManager
    assert isinstance(browser_manager, BrowserSessionManager)
    pdf_url = browser_manager.discover_pdf_url(page_url.strip(), doi=doi)
    if pdf_url:
        return 200, {"pdf_url": pdf_url}
    return 200, {"pdf_url": None}


def _handle_browser_download_post(
    body: Any, browser_manager: object | None,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(body, dict):
        return 400, {"error": "body must be a JSON object"}
    if browser_manager is None:
        return 503, {"error": "browser session not available"}
    pdf_url = body.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip():
        return 400, {"error": "pdf_url required"}
    from pzi.browser_session_manager import BrowserSessionManager
    assert isinstance(browser_manager, BrowserSessionManager)
    pdf_bytes = browser_manager.download_pdf_bytes(pdf_url.strip())
    if pdf_bytes:
        import base64
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
    dry_run = bool(body.get("dry_run", False))

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
    status = 200 if result["status"] == "ok" else 404
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
            result,
            body=body,
            safe_pdf_candidates=safe_pdf_candidates,
            attach_session_store=attach_session_store,
            request_id_factory=request_id_factory,
            token_factory=token_factory,
            now=now,
        )
    status = 200 if result["status"] == "ok" else 400
    return status, capture_payload(
        result, include_diagnostics=bool(body.get("verbose", False))
    )


def _maybe_add_pdf_request(
    result: dict[str, Any],
    *,
    body: dict[str, Any],
    safe_pdf_candidates: list[str] | None,
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
        attach_base_url="http://127.0.0.1:8765/attach-pdf-raw",
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
        allowed_source_urls=[str(c["url"]) for c in plan["candidates"]],
    )
    attach_session_store.put(session)
    result["pdf_request"] = plan


def _handle_attach_pdf_post(
    body: Any, config_path: str, home_dir: str,
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
    result = attach_pdf_bytes(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        pdf_base64=pdf_base64,
        source_url=source_url,
    )
    status = 200 if result["status"] == "ok" else 400
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
        token = body.get("attach_token") if isinstance(body.get("attach_token"), str) else ""
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
    status = 200 if result["status"] == "ok" else 400
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
    status = 200 if result["status"] == "ok" else 400
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
    status = 200 if result["status"] == "ok" else 400
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
    status = 200 if result["status"] == "ok" else 400
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
    status = 200 if result["status"] == "ok" else 400
    return status, promote_payload(
        result, include_diagnostics=bool(body.get("verbose", False))
    )


def _handle_post(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
) -> None:
    idle_state = getattr(request, "_idle_state", None)
    if idle_state is not None:
        idle_state["_last_request"] = time.monotonic()

    error = request_security_error(
        method="POST", headers=dict(request.headers.items()), security=security
    )
    if error is not None:  # pragma: no cover — covered by integration
        _respond(request, error[0], {"error": error[1]}, security)
        return
    client_id = request.client_address[0] if request.client_address else "unknown"
    allowed, post_remaining, post_reset = request._rate_limiter.check(client_id)  # type: ignore[attr-defined]
    if not allowed:
        _respond(request, 429, {"error": "rate limit exceeded"}, security,
                 rate_remaining=0, rate_reset=post_reset)
        return
    length_result = validated_content_length(
        request.headers.get("Content-Length"), max_body_bytes=security["max_body_bytes"]
    )
    if isinstance(length_result, tuple):
        _respond(request, length_result[0], {"error": length_result[1]}, security)
        return
    length = length_result
    raw = request.rfile.read(length) if length > 0 else b""
    parsed_path = urlsplit(request.path)
    if parsed_path.path == "/attach-pdf-raw":
        query = parse_qs(parsed_path.query)
        body = {
            "request_id": query.get("request_id", [None])[0],
            "attach_token": request.headers.get("X-Pzi-Attach-Token")
            or query.get("attach_token", [None])[0],
            "citekey": query.get("citekey", [None])[0],
            "bib": query.get("bib", [None])[0],
            "source_url": query.get("source_url", [None])[0],
            "pdf_bytes": raw,
        }
        status, response_body = process_post_request(
            parsed_path.path,
            body,
            config_path,
            home_dir,
            attach_session_store=getattr(request, "_attach_session_store", None),
        )
        _respond(request, status, response_body, security,
                 rate_remaining=post_remaining, rate_reset=post_reset)
        return
    try:
        body: Any = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        _respond(request, 400, {"error": "invalid JSON body"}, security)
        return

    status, response_body = process_post_request(
        request.path, body, config_path, home_dir,
        browser_manager=getattr(request, "_browser_session_manager", None),
        attach_session_store=getattr(request, "_attach_session_store", None),
    )
    _respond(request, status, response_body, security,
             rate_remaining=post_remaining, rate_reset=post_reset)


def _new_request_id() -> str:
    return secrets.token_urlsafe(18)


def _new_attach_token() -> str:
    return secrets.token_urlsafe(32)


def _send_cors_headers(request: BaseHTTPRequestHandler, security: HttpSecurityConfig) -> None:
    origin = request.headers.get("Origin")
    if origin_allowed(origin, security["allowed_origins"]):
        request.send_header("Access-Control-Allow-Origin", origin or "http://127.0.0.1")
        request.send_header("Vary", "Origin")
    request.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    request.send_header(
        "Access-Control-Allow-Headers", f"Content-Type, {AUTH_HEADER}, Authorization"
    )


def _respond(
    request: BaseHTTPRequestHandler, status: int, data: Any, security: HttpSecurityConfig,
    rate_remaining: int | None = None, rate_reset: int | None = None,
) -> None:
    body = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(body)))
    if rate_remaining is not None:
        request.send_header("X-RateLimit-Remaining", str(rate_remaining))
    if rate_reset is not None:
        request.send_header("X-RateLimit-Reset", str(rate_reset))
    if status == 429:
        request.send_header("Retry-After", str(rate_reset or 60))
    _send_cors_headers(request, security)
    request.end_headers()
    try:
        request.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        return


def _health_payload(config_path: str, home_dir: str) -> dict[str, Any]:
    result = doctor_check(config_path=config_path, home_dir=home_dir)
    return {
        "status": result["status"],
        "config_ok": result["config_ok"],
        "config_errors": result["config_errors"],
        "translation_server_url": result["translation_server_url"],
        "translation_server_reachable": result["translation_server_reachable"],
    }


def run_server(  # pragma: no cover — I/O entry point, covered by integration tests
    *,
    config_path: str,
    home_dir: str,
    host: str,
    port: int,
    server_class: type[HTTPServer] = ThreadingHTTPServer,
    security: HttpSecurityConfig | None = None,
    idle_minutes: int | None = None,
    on_shutdown: Callable[[], None] | None = None,
    browser_profile_path: str | None = None,
    browser_engine: str = "chromium",
) -> None:
    # Create persistent browser session manager (lazily launched).
    from pzi.browser_session_manager import BrowserSessionManager

    browser_manager = BrowserSessionManager(
        browser=browser_engine,
        profile_path=browser_profile_path,
        headless=True,
    )

    handler = build_handler_class(
        config_path=config_path,
        home_dir=home_dir,
        security=security,
        browser_manager=browser_manager,
    )
    idle_state: dict[str, float] | None = None
    if idle_minutes is not None:
        idle_state = {"_last_request": time.monotonic()}
        handler._idle_state = idle_state  # type: ignore[attr-defined]

    server = server_class((host, port), handler)
    server.socket.settimeout(30)

    def _shutdown() -> None:
        browser_manager.close()
        if on_shutdown is not None:
            on_shutdown()

    if idle_state is not None:
        assert idle_minutes is not None  # guarded by idle_state is not None
        _start_idle_monitor(server, idle_state, idle_minutes, _shutdown)

    try:
        server.serve_forever()
    finally:
        browser_manager.close()
        server.server_close()


def _start_idle_monitor(
    server: HTTPServer,
    idle_state: dict[str, float],
    idle_minutes: int,
    on_shutdown: Callable[[], None] | None,
) -> None:
    import threading

    def _monitor() -> None:
        while True:
            time.sleep(30)
            elapsed = time.monotonic() - idle_state["_last_request"]
            if elapsed > idle_minutes * 60:
                server.shutdown()
                if on_shutdown is not None:
                    on_shutdown()
                return

    t = threading.Thread(target=_monitor, daemon=True)
    t.start()
