"""Local HTTP capture API backed by the same service pipeline as the CLI."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pzi.add_service import add_input_to_bib
from pzi.bib_repository import read_bib_file
from pzi.bib_service import list_bibs
from pzi.config import load_and_resolve_bib
from pzi.doctor_service import doctor_check
from pzi.http_payloads import (
    capture_payload,
    detail_payload,
    entries_payload,
    metadata_url_override_error,
    pdf_url_candidates_from_body,
    promote_payload,
    record_overrides_from_capture_body,
    search_payload,
    tag_change_payload,
    tag_list_payload,
    update_payload,
)
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
from pzi.pdf_service import attach_pdf_bytes, attach_pdf_raw_bytes
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib
from pzi.tag_service import add_tags, list_tags, remove_tags
from pzi.update_service import update_bib


def build_handler_class(
    *,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig | None = None,
) -> type[BaseHTTPRequestHandler]:
    security_config = security or build_http_security_config()
    return type(
        "PziHandler",
        (BaseHTTPRequestHandler,),
        {
            "server_version": "pzi/0.1",
            "_rate_limiter": RateLimiter(max_requests=security_config["rate_limit_rpm"]),
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


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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
    status, body = process_get_request(request.path, config_path, home_dir)
    _respond(request, status, body, security,
             rate_remaining=remaining, rate_reset=reset)


def process_post_request(
    path: str,
    body: Any,
    config_path: str,
    home_dir: str,
) -> tuple[int, dict[str, Any]]:
    """Pure: process a POST request body, returning (status, body_dict).

    Testable without a server socket. Used by _handle_post.
    """
    parsed = urlsplit(path)
    p = parsed.path

    if p == "/capture":
        return _handle_capture_post(body, config_path, home_dir)

    if p == "/attach-pdf-bytes":
        return _handle_attach_pdf_post(body, config_path, home_dir)

    if p == "/attach-pdf-raw":
        return _handle_attach_pdf_raw_post(body, config_path, home_dir)

    if p == "/tags/add":
        return _handle_tags_add_post(body, config_path, home_dir)

    if p == "/tags/remove":
        return _handle_tags_remove_post(body, config_path, home_dir)

    if p == "/update":
        return _handle_update_post(body, config_path, home_dir)

    if p == "/promote":
        return _handle_promote_post(body, config_path, home_dir)

    return 404, {"error": "not found"}


def _handle_capture_post(
    body: Any, config_path: str, home_dir: str,
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
    raw_cookies = body.get("cookies")
    cookies = raw_cookies if isinstance(raw_cookies, str) and raw_cookies.strip() else None
    result = add_input_to_bib(
        config_path=config_path,
        home_dir=home_dir,
        value=stripped_url,
        record_overrides=record_overrides_from_capture_body(body),
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        dry_run=bool(body.get("dry_run", False)),
        pdf_url_candidates=safe_pdf_candidates,
        browser=browser,
        cookies=cookies,
    )
    status = 200 if result["status"] == "ok" else 400
    return status, capture_payload(
        result, include_diagnostics=bool(body.get("verbose", False))
    )


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
    body: Any, config_path: str, home_dir: str,
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
    result = attach_pdf_raw_bytes(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
        citekey=citekey,
        pdf_bytes=pdf_bytes,
        source_url=source_url,
    )
    status = 200 if result["status"] == "ok" else 400
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
            "citekey": query.get("citekey", [None])[0],
            "bib": query.get("bib", [None])[0],
            "source_url": query.get("source_url", [None])[0],
            "pdf_bytes": raw,
        }
        status, response_body = process_post_request(
            parsed_path.path, body, config_path, home_dir
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
        request.path, body, config_path, home_dir
    )
    _respond(request, status, response_body, security,
             rate_remaining=post_remaining, rate_reset=post_reset)


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
) -> None:
    handler = build_handler_class(config_path=config_path, home_dir=home_dir, security=security)
    idle_state: dict[str, float] | None = None
    if idle_minutes is not None:
        idle_state = {"_last_request": time.monotonic()}
        handler._idle_state = idle_state  # type: ignore[attr-defined]

    server = server_class((host, port), handler)
    server.socket.settimeout(30)

    if idle_state is not None:
        assert idle_minutes is not None  # guarded by idle_state is not None
        _start_idle_monitor(server, idle_state, idle_minutes, on_shutdown)

    try:
        server.serve_forever()
    finally:
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
