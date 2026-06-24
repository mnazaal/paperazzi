"""Local HTTP capture API backed by the same service pipeline as the CLI."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from pzi.http_binary_routes import (
    ExportBytesResponse,
    PdfFileResponse,
    build_export_bytes_response,
    build_pdf_file_response,
)
from pzi.http_get_routes import process_get_request
from pzi.http_post_routes import process_post_request
from pzi.http_security import (
    AUTH_HEADER,
    HttpSecurityConfig,
    RateLimiter,
    build_http_security_config,
    loopback_bind_host,
    origin_allowed,
    request_security_error,
    validated_content_length,
)
from pzi.pdf_attach_session_store import AttachSessionStore

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def server_exposure_error(host: str, security: HttpSecurityConfig) -> str | None:
    """Return refusal reason for unsafe direct server exposure, if any."""
    if security.get("auth_token") or loopback_bind_host(host):
        return None
    return (
        "refusing to serve unauthenticated API on a non-loopback host; "
        "set api_auth_token or bind to 127.0.0.1/localhost"
    )


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


def _serve_pdf(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    citekey: str,
    bib_selector: str | None,
    security: HttpSecurityConfig,
    *,
    rate_remaining: int | None = None,
    rate_reset: int | None = None,
) -> None:
    """Serve a PDF file for a citekey."""
    if not citekey:
        request.send_response(400)
        request.end_headers()
        return

    status, response = build_pdf_file_response(
        config_path=config_path,
        home_dir=home_dir,
        citekey=citekey,
        bib_selector=bib_selector,
    )
    if not isinstance(response, PdfFileResponse):
        request.send_response(status)
        request.end_headers()
        return

    try:
        size = response.path.stat().st_size
    except OSError:
        request.send_response(500)
        request.end_headers()
        return

    request.send_response(200)
    request.send_header("Content-Type", response.content_type)
    request.send_header("Content-Length", str(size))
    request.send_header(
        "Content-Disposition",
        f'inline; filename="{response.filename}"',
    )
    _send_cors_headers(request, security)
    if rate_remaining is not None:
        request.send_header("X-RateLimit-Remaining", str(rate_remaining))
    if rate_reset is not None:
        request.send_header("X-RateLimit-Reset", str(rate_reset))
    request.end_headers()
    try:
        with response.path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                request.wfile.write(chunk)
    except OSError:
        return


def _serve_export_raw(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    fmt: str,
    bib_selector: str | None,
    security: HttpSecurityConfig,
    *,
    rate_remaining: int | None = None,
    rate_reset: int | None = None,
) -> None:
    status, response = build_export_bytes_response(
        config_path=config_path,
        home_dir=home_dir,
        fmt=fmt,
        bib_selector=bib_selector,
    )
    if not isinstance(response, ExportBytesResponse):
        _respond(
            request,
            status,
            response,
            security,
            rate_remaining=rate_remaining,
            rate_reset=rate_reset,
        )
        return

    request.send_response(200)
    request.send_header("Content-Type", response.content_type)
    request.send_header("Content-Length", str(len(response.content)))
    request.send_header(
        "Content-Disposition",
        f'inline; filename="{response.filename}"',
    )
    _send_cors_headers(request, security)
    if rate_remaining is not None:
        request.send_header("X-RateLimit-Remaining", str(rate_remaining))
    if rate_reset is not None:
        request.send_header("X-RateLimit-Reset", str(rate_reset))
    request.end_headers()
    try:
        request.wfile.write(response.content)
    except (BrokenPipeError, ConnectionResetError):
        return


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
        qs_raw = parse_qs(urlsplit(request.path).query)
        bib = qs_raw.get("bib", [None])[0] if qs_raw.get("bib") else None
        _serve_pdf(
            request,
            config_path,
            home_dir,
            citekey,
            bib,
            security,
            rate_remaining=remaining,
            rate_reset=reset,
        )
        return
    if p == "/export/raw":
        qs_raw = parse_qs(urlsplit(request.path).query)
        fmt = qs_raw.get("format", ["bibtex"])[0] or "bibtex"
        bib = qs_raw.get("bib", [None])[0] if qs_raw.get("bib") else None
        _serve_export_raw(
            request,
            config_path,
            home_dir,
            fmt,
            bib,
            security,
            rate_remaining=remaining,
            rate_reset=reset,
        )
        return

    status, body = process_get_request(request.path, config_path, home_dir)
    _respond(request, status, body, security,
             rate_remaining=remaining, rate_reset=reset)


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
    client_id = request.client_address[0] if request.client_address else "unknown"
    allowed, post_remaining, post_reset = request._rate_limiter.check(client_id)  # type: ignore[attr-defined]
    if not allowed:
        _respond(request, 429, {"error": "rate limit exceeded"}, security,
                 rate_remaining=0, rate_reset=post_reset)
        return
    # Only count an accepted request against the idle-stop timer (mirrors GET),
    # so a rejected POST can't keep the auto-stop server alive.
    idle_state = getattr(request, "_idle_state", None)
    if idle_state is not None:
        idle_state["_last_request"] = time.monotonic()
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


def _send_cors_headers(request: BaseHTTPRequestHandler, security: HttpSecurityConfig) -> None:
    origin = request.headers.get("Origin")
    if origin_allowed(origin, security["allowed_origins"]):
        request.send_header("Access-Control-Allow-Origin", origin or "http://127.0.0.1")
        request.send_header("Vary", "Origin")
    request.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    request.send_header(
        "Access-Control-Allow-Headers",
        f"Content-Type, {AUTH_HEADER}, X-Pzi-Attach-Token, Authorization",
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
    security_config = security or build_http_security_config()
    exposure_error = server_exposure_error(host, security_config)
    if exposure_error is not None:
        raise ValueError(exposure_error)

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
        security=security_config,
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
