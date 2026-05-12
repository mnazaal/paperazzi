"""Local HTTP capture API backed by the same service pipeline as the CLI."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any, TypedDict

from pzi.add_service import AddRecordResult, add_input_to_bib
from pzi.bib_service import list_bibs
from pzi.doctor_service import doctor_check
from pzi.pdf_service import attach_pdf_bytes

DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1",
    "http://localhost",
    "chrome-extension://",
    "moz-extension://",
)
DEFAULT_MAX_BODY_BYTES = 5 * 1024 * 1024
AUTH_HEADER = "X-Pzi-Token"


class HttpSecurityConfig(TypedDict):
    auth_token: str | None
    allowed_origins: tuple[str, ...]
    max_body_bytes: int


def build_http_security_config(
    *,
    auth_token: str | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> HttpSecurityConfig:
    """Normalize HTTP security knobs without touching request state."""
    origins = tuple(
        origin.strip()
        for origin in (allowed_origins or DEFAULT_ALLOWED_ORIGINS)
        if isinstance(origin, str) and origin.strip()
    )
    normalized_token = (
        auth_token.strip()
        if isinstance(auth_token, str) and auth_token.strip()
        else None
    )
    return {
        "auth_token": normalized_token,
        "allowed_origins": origins,
        "max_body_bytes": max(0, int(max_body_bytes)),
    }


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
    if path == "/health":
        return 200, _health_payload(config_path, home_dir)
    if path == "/bibs":
        result = list_bibs(config_path=config_path, home_dir=home_dir)
        status = 200 if result["status"] == "ok" else 500
        return status, result
    return 404, {"error": "not found"}


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
    status, body = process_get_request(request.path, config_path, home_dir)
    _respond(request, status, body, security)


def process_post_request(
    path: str,
    body: Any,
    config_path: str,
    home_dir: str,
) -> tuple[int, dict[str, Any]]:
    """Pure: process a POST request body, returning (status, body_dict).

    Testable without a server socket. Used by _handle_post.
    """
    if path == "/capture":
        if not isinstance(body, dict):
            return 400, {"error": "capture body must be a JSON object"}
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            return 400, {"error": "url required"}
        result = add_input_to_bib(
            config_path=config_path,
            home_dir=home_dir,
            value=url,
            record_overrides=_record_overrides_from_capture_body(body),
            bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
            dry_run=bool(body.get("dry_run", False)),
            pdf_url_candidates=_pdf_url_candidates_from_body(body),
        )
        status = 200 if result["status"] == "ok" else 400
        return status, _capture_payload(result)

    if path == "/attach-pdf-bytes":
        if not isinstance(body, dict):
            return 400, {"error": "attach body must be a JSON object"}
        citekey = body.get("citekey")
        pdf_base64 = body.get("pdf_base64")
        if not isinstance(citekey, str) or not citekey.strip():
            return 400, {"error": "citekey required"}
        if not isinstance(pdf_base64, str) or not pdf_base64.strip():
            return 400, {"error": "pdf_base64 required"}
        result = attach_pdf_bytes(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=body.get("bib") if isinstance(body.get("bib"), str) else None,
            citekey=citekey,
            pdf_base64=pdf_base64,
            source_url=body.get("source_url") if isinstance(body.get("source_url"), str) else None,
        )
        status = 200 if result["status"] == "ok" else 400
        return status, result

    return 404, {"error": "not found"}


def _handle_post(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
) -> None:
    error = request_security_error(
        method="POST", headers=dict(request.headers.items()), security=security
    )
    if error is not None:  # pragma: no cover — covered by integration
        _respond(request, error[0], {"error": error[1]}, security)
        return
    length_result = validated_content_length(
        request.headers.get("Content-Length"), max_body_bytes=security["max_body_bytes"]
    )
    if isinstance(length_result, tuple):
        _respond(request, length_result[0], {"error": length_result[1]}, security)
        return
    length = length_result
    raw = request.rfile.read(length) if length > 0 else b""
    try:
        body: Any = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        _respond(request, 400, {"error": "invalid JSON body"}, security)
        return

    status, response_body = process_post_request(
        request.path, body, config_path, home_dir
    )
    _respond(request, status, response_body, security)


def _record_overrides_from_capture_body(body: dict[str, Any]) -> dict[str, object]:
    record_overrides: dict[str, object] = {}
    raw_tags = body.get("tags")
    if isinstance(raw_tags, list):
        record_overrides["tags"] = [tag for tag in raw_tags if isinstance(tag, str) and tag.strip()]
    for body_key, record_key in [
        ("page_title", "title"),
        ("canonical_url", "canonical_url"),
        ("source_url", "source_url"),
        ("abstract_url", "abstract_url"),
        ("doi", "doi"),
    ]:
        value = body.get(body_key)
        if isinstance(value, str) and value.strip():
            record_overrides[record_key] = value.strip()
    return record_overrides


def _pdf_url_candidates_from_body(body: dict[str, Any]) -> list[str] | None:
    raw_candidates = body.get("pdf_url_candidates")
    if not isinstance(raw_candidates, list):
        return None
    return [
        candidate
        for candidate in raw_candidates
        if isinstance(candidate, str) and candidate.strip()
    ]


def origin_allowed(origin: str | None, allowed_origins: tuple[str, ...]) -> bool:
    """Return whether Origin is acceptable for local API access."""
    if origin is None or not origin.strip():
        return True
    value = origin.strip()
    return any(
        value == allowed.rstrip("/") or value.startswith(allowed)
        for allowed in allowed_origins
    )


def request_security_error(
    *, method: str, headers: dict[str, str], security: HttpSecurityConfig
) -> tuple[int, str] | None:
    """Pure request gate: origin + optional bearer/header token."""
    origin = headers.get("Origin") or headers.get("origin")
    if not origin_allowed(origin, security["allowed_origins"]):
        return 403, "origin not allowed"
    if method.upper() == "OPTIONS":
        return None
    token = security["auth_token"]
    if token is None:
        return None
    supplied = headers.get(AUTH_HEADER) or headers.get(AUTH_HEADER.lower())
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        supplied = auth.removeprefix("Bearer ")
    if supplied is None or not hmac.compare_digest(supplied, token):
        return 401, "invalid API token"
    return None


def validated_content_length(value: str | None, *, max_body_bytes: int) -> int | tuple[int, str]:
    if value is None or not value.strip():
        return 0
    try:
        length = int(value)
    except ValueError:
        return 400, "invalid Content-Length"
    if length < 0:
        return 400, "invalid Content-Length"
    if length > max_body_bytes:
        return 413, "request body too large"
    return length


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
    request: BaseHTTPRequestHandler, status: int, data: Any, security: HttpSecurityConfig
) -> None:
    body = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    request.send_response(status)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(body)))
    _send_cors_headers(request, security)
    request.end_headers()
    request.wfile.write(body)


def _capture_payload(result: AddRecordResult) -> dict[str, Any]:
    return {
        "status": result["status"],
        "bib": result["bib_name"],
        "citekey": result["citekey"],
        "action": result["action"],
        "pdf_path": result["pdf_path"],
        "dry_run": result["dry_run"],
        "message": result["message"],
        "warnings": result["warnings"],
        "errors": result["errors"],
    }


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
) -> None:
    handler = build_handler_class(config_path=config_path, home_dir=home_dir, security=security)
    server = server_class((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
