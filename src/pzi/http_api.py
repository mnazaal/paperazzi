"""Local HTTP capture API backed by the same service pipeline as the CLI."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any, Protocol, TextIO
from urllib.parse import parse_qs, urlsplit

from pzi.http_binary_routes import (
    ExportBytesResponse,
    PdfFileResponse,
    build_export_bytes_response,
    build_pdf_file_response,
)
from pzi.http_get_routes import (
    BINARY_GET_ROUTES,
    decode_path_segment,
    process_get_request,
)
from pzi.http_post_routes import process_post_request
from pzi.http_security import (
    AUTH_HEADER,
    HttpSecurityConfig,
    build_http_security_config,
    duplicate_host_error,
    loopback_bind_host,
    origin_allowed,
    request_security_error,
    validated_content_length,
)
from pzi.pdf_attach_session_store import AttachSessionStore


class BinaryGetHandler(Protocol):
    """What a binary GET handler is called with.

    One signature for both, so the dispatcher can key into a table rather than
    branch on the route name. *suffix* is whatever followed a prefix route's
    path, still percent-encoded.
    """

    def __call__(
        self,
        request: BaseHTTPRequestHandler,
        config_path: str,
        home_dir: str,
        security: HttpSecurityConfig,
        *,
        suffix: str,
        query: dict[str, list[str]],
    ) -> None: ...


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

# Per-connection read timeout (slowloris mitigation): a client that opens a
# connection and then trickles bytes in (or none at all) would otherwise hold
# a handler thread/socket open indefinitely, since `server.socket.settimeout`
# below only bounds accept() on the *listening* socket, not reads on sockets
# already accepted. `socketserver.StreamRequestHandler.setup()` applies the
# `timeout` class attribute to each accepted connection via
# `self.connection.settimeout(...)`, so setting it here bounds every
# individual read/write on that connection.
CONNECTION_READ_TIMEOUT_SECONDS = 30
#: How often the `--stop-after` watchdog checks for idleness. Distinct from the
#: two other 30s values in this module (the per-connection read timeout above and
#: the `accept()` timeout in `run_server`) — they are unrelated despite matching.
IDLE_POLL_SECONDS = 30


def server_exposure_error(host: str, security: HttpSecurityConfig) -> str | None:
    """Return refusal reason for unsafe direct server exposure, if any.

    Two separate refusals. A wildcard bind is refused outright, token or not:
    binding every interface is not something a token makes safe, and it is what
    `docs/security.md` tells the reader cannot happen. Then, for a specific
    address, an unauthenticated non-loopback bind is refused.

    `cli_server.build_server_plan` applies the same two refusals to the CLI's
    own arguments; both entry points into `run_server` have to agree.
    """
    from pzi.cli_server import is_wildcard_bind

    if is_wildcard_bind(host):
        return (
            f"refusing to bind every interface ({host!r}); "
            "bind a specific address, or 127.0.0.1 for local use"
        )
    if security.get("auth_token") or loopback_bind_host(host):
        return None
    return (
        "refusing to serve unauthenticated API on a non-loopback host; "
        "set api_auth_token or bind to 127.0.0.1/localhost"
    )


def _recording_send_response(
    request: BaseHTTPRequestHandler, code: int, message: str | None = None
) -> None:
    """`send_response` that remembers the status it sent."""
    request._pzi_status = code  # type: ignore[attr-defined]
    BaseHTTPRequestHandler.send_response(request, code, message)


def _log_request(
    request: BaseHTTPRequestHandler, started_at: float, log_to: TextIO
) -> None:
    """One line per request: method, path, status, milliseconds.

    Off unless `pzi server --log-requests` asked for it. The query string is
    dropped rather than logged: it carries `bib=` and `format=`, and it is the
    only place an attach token could appear. That is a partial protection, not
    a general one — `/detail/<citekey>`, `/pdf/<citekey>` and `/tags/<citekey>`
    carry the citekey in the *path*, so a log of those three does name what was
    read.

    `BaseHTTPRequestHandler` records the status in `_status` via
    `send_response`; a request that died before responding has none, which is
    itself worth seeing, so it logs as `-`.
    """
    elapsed_ms = (time.monotonic() - started_at) * 1000
    raw_path = getattr(request, "path", None)
    if raw_path is None:
        # A request line the parser rejected never set one. It still logs,
        # because "someone sent this server something it could not parse" is
        # exactly what a request log is for.
        path = "-"
    else:
        try:
            path = urlsplit(raw_path).path or "/"
        except ValueError:
            path = "(unparseable)"
    status = getattr(request, "_pzi_status", None)
    print(
        f"{getattr(request, 'command', None) or '-'} {path} "
        f"{status if status is not None else '-'} {elapsed_ms:.0f}ms",
        file=log_to,
    )


def build_handler_class(
    *,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig | None = None,
    browser_manager: object | None = None,
    attach_session_store: AttachSessionStore | None = None,
    log_requests_to: TextIO | None = None,
) -> type[BaseHTTPRequestHandler]:
    security_config = security or build_http_security_config()
    store = attach_session_store or AttachSessionStore()

    def _timed(handler: Callable[[], None], request: BaseHTTPRequestHandler) -> None:
        """Run a guarded handler, logging it afterwards when asked.

        Wraps `_guarded` rather than replacing it: the 500 guard must still run
        first, so a handler that raises is logged with the status the guard
        sent rather than not logged at all.
        """
        if log_requests_to is None:
            _guarded(handler, request, security_config)
            return
        started_at = time.monotonic()
        try:
            _guarded(handler, request, security_config)
        finally:
            _log_request(request, started_at, log_requests_to)

    def _error_response(
        request: BaseHTTPRequestHandler, code: int, message: str | None
    ) -> None:
        """`send_error`, logged like any other response.

        These requests never reach `_timed`, so without this a `PUT` or an
        unparseable request line was the one thing `--log-requests` could not
        show you.
        """
        if log_requests_to is None:
            _send_error_as_json(request, code, security_config, message)
            return
        started_at = time.monotonic()
        try:
            _send_error_as_json(request, code, security_config, message)
        finally:
            _log_request(request, started_at, log_requests_to)

    return type(
        "PziHandler",
        (BaseHTTPRequestHandler,),
        {
            # No version on either half. `Server: pzi/0.1 Python/3.12.3` named
            # the exact interpreter patch level to any caller, before the token
            # was checked, and the `0.1` had been wrong since 0.2.0 shipped.
            "server_version": "pzi",
            "sys_version": "",
            "timeout": CONNECTION_READ_TIMEOUT_SECONDS,
            "_browser_session_manager": browser_manager,
            "_attach_session_store": store,
            # Guarded like the other three. Latent — `_handle_options` could
            # not be made to raise — but the reason `_guarded` exists (an
            # unguarded exception closes the socket having sent zero bytes,
            # which a client cannot distinguish from the server being down)
            # applies to every handler or to none.
            "do_OPTIONS": lambda request: _timed(
                lambda: _handle_options(request, security_config), request
            ),
            "do_GET": lambda request: _timed(
                lambda: _handle_get(request, config_path, home_dir, security_config), request
            ),
            "do_POST": lambda request: _timed(
                lambda: _handle_post(request, config_path, home_dir, security_config), request
            ),
            # Record the status for `_log_request`. Overriding the one method
            # every response path already goes through beats stamping it at
            # each `_respond` call site, which would silently miss any new one.
            "send_response": _recording_send_response,
            # Everything that never reaches a `do_*` method — an unsupported
            # verb, a request line the parser rejected — answers here, and by
            # default that was an HTML page. Same JSON contract as every other
            # response, and the `--log-requests` line comes with it, because
            # `send_error` goes through `send_response` too.
            "send_error": lambda request, code, message=None, explain=None: (
                _error_response(request, code, message)
            ),
            "log_message": lambda request, format, *args: None,
        },
    )


def _send_error_as_json(
    request: BaseHTTPRequestHandler,
    code: int,
    security: HttpSecurityConfig,
    message: str | None = None,
) -> None:
    """Answer a `send_error` in the same JSON shape `_respond` uses.

    `BaseHTTPRequestHandler.send_error` handles `PUT`, `HEAD`, a request line
    the parser rejected and a request URI over 65536 bytes — none of which
    reach a `do_*` method. It wrote a 356-byte HTML page with no CORS headers
    and no `X-Content-Type-Options`, so the requests a client had no other way
    to diagnose were the ones it could not read the answer to.

    HEAD gets the headers and no body: a body on a HEAD response is a protocol
    violation, and the stdlib suppresses it for the same three status classes.
    A request line that never parsed leaves `request_version` at HTTP/0.9,
    where the stdlib sends neither status line nor headers — so that case is
    the JSON document alone, which is still not an HTML page naming the
    interpreter.
    """
    phrase = request.responses.get(code, ("", ""))[0] if code in request.responses else ""
    body = json.dumps({"error": message or phrase or f"HTTP {code}"}).encode("utf-8")
    request._response_started = True  # type: ignore[attr-defined]
    request.send_response(code, message)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(body)))
    request.send_header("X-Content-Type-Options", "nosniff")
    _send_cors_headers(request, security)
    request.end_headers()
    if getattr(request, "command", None) == "HEAD" or code < 200 or code in (204, 205, 304):
        return
    try:
        request.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        return


def _request_gate(
    request: BaseHTTPRequestHandler, method: str, security: HttpSecurityConfig
) -> tuple[int, str] | None:
    """The one gate all three handlers go through.

    Flattening headers with `dict(request.headers.items())` throws away every
    duplicate but the last, so the duplicate-`Host` check has to happen here,
    on the multi-valued header object, before the flattening.
    """
    error = duplicate_host_error(request.headers.get_all("host") or [])
    if error is not None:
        return error
    return request_security_error(
        method=method, headers=dict(request.headers.items()), security=security
    )


def _guarded(
    handler: Callable[[], None],
    request: BaseHTTPRequestHandler,
    security: HttpSecurityConfig,
) -> None:
    """Run a request handler, turning an unexpected failure into a 500.

    `BaseHTTPRequestHandler.handle_one_request` catches only `TimeoutError`, so
    any other exception escapes to `handle_error()` and closes the socket having
    sent **zero bytes** — which a client cannot distinguish from the server not
    running. Every service call below this point is otherwise unguarded.

    The exception text is deliberately not sent to the client: it can name local
    paths and internals. The traceback still reaches stderr via `handle_error`.
    """
    # NOTE: the handler serves HTTP/1.0 and stays there — `protocol_version` is
    # never set, so `BaseHTTPRequestHandler`'s default stands and every
    # connection closes after one response. Decided rather than left open: the
    # only client is one browser extension, `CONNECTION_READ_TIMEOUT_SECONDS`
    # already bounds how long a connection can be held, and keep-alive bought
    # nothing but a set of `close_connection=True` arguments that did nothing.
    # Those are gone. Setting `"HTTP/1.1"` would put them back.
    #
    # Reset per request rather than per connection anyway: it costs one
    # assignment and it is what makes the 500 guard correct if that ever changes.
    request._response_started = False  # type: ignore[attr-defined]
    try:
        handler()
    except Exception:  # boundary of last resort; re-raised below
        if getattr(request, "_response_started", False):
            # Headers (and possibly a partial body) already went out — a second
            # `send_response` would corrupt the stream. Let the connection drop
            # and report the traceback the normal way.
            raise
        _respond(request, 500, {"error": "internal server error"}, security)
        raise


def _handle_options(request: BaseHTTPRequestHandler, security: HttpSecurityConfig) -> None:
    error = _request_gate(request, "OPTIONS", security)
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
) -> None:
    """Serve a PDF file for a citekey."""
    # The empty-citekey case is already handled by `build_pdf_file_response`,
    # which returns a proper JSON error for it.
    status, response = build_pdf_file_response(
        config_path=config_path,
        home_dir=home_dir,
        citekey=citekey,
        bib_selector=bib_selector,
    )
    if not isinstance(response, PdfFileResponse):
        # `response` is already the JSON error the planner built; send it, with
        # the CORS headers, so a cross-origin caller can read why. Mirrors
        # `_serve_export_raw`.
        _respond(request, status, response, security)
        return

    try:
        size = response.path.stat().st_size
    except OSError:
        _respond(
            request, 500, {"error": "PDF could not be read"}, security,
        )
        return

    request._response_started = True  # type: ignore[attr-defined]
    request.send_response(200)
    request.send_header("Content-Type", response.content_type)
    request.send_header("Content-Length", str(size))
    request.send_header("X-Content-Type-Options", "nosniff")
    request.send_header(
        "Content-Disposition",
        f'inline; filename="{response.filename}"',
    )
    _send_cors_headers(request, security)
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
        )
        return

    request._response_started = True  # type: ignore[attr-defined]
    request.send_response(200)
    request.send_header("Content-Type", response.content_type)
    request.send_header("Content-Length", str(len(response.content)))
    request.send_header("X-Content-Type-Options", "nosniff")
    request.send_header(
        "Content-Disposition",
        f'inline; filename="{response.filename}"',
    )
    _send_cors_headers(request, security)
    request.end_headers()
    try:
        request.wfile.write(response.content)
    except (BrokenPipeError, ConnectionResetError):
        return


def _serve_pdf_route(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
    *,
    suffix: str,
    query: dict[str, list[str]],
) -> None:
    _serve_pdf(
        request,
        config_path,
        home_dir,
        decode_path_segment(suffix),
        query.get("bib", [None])[0],
        security,
    )


def _serve_export_raw_route(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
    *,
    suffix: str,
    query: dict[str, list[str]],
) -> None:
    _serve_export_raw(
        request,
        config_path,
        home_dir,
        query.get("format", ["bibtex"])[0] or "bibtex",
        query.get("bib", [None])[0],
        security,
    )


#: The table `BinaryGetRoute.name` is a key into — the surface is declared in
#: `http_get_routes.BINARY_GET_ROUTES`, the plumbing lives here because these
#: write bytes to the socket rather than returning `(status, dict)`. Keyed
#: rather than branched, so a route declared with no handler is a 404 instead
#: of whatever the `else` arm happened to be.
BINARY_GET_HANDLERS: dict[str, BinaryGetHandler] = {
    "pdf": _serve_pdf_route,
    "export_raw": _serve_export_raw_route,
}


def _handle_get(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
) -> None:
    error = _request_gate(request, "GET", security)
    if error is not None:
        _respond(request, error[0], {"error": error[1]}, security)
        return
    idle_state = getattr(request, "_idle_state", None)
    if idle_state is not None:
        idle_state["_last_request"] = time.monotonic()

    # `GET http://[evil/health` is a legal HTTP/1.1 absolute-form target and
    # `urlsplit` raises `ValueError` on it. `_host_only` guards this already;
    # the route dispatchers did not, so a malformed target was a 500 with a
    # traceback rather than a 400.
    try:
        p = urlsplit(request.path).path
    except ValueError:
        _respond(request, 400, {"error": "invalid request target"}, security)
        return
    # The binary GETs, from the same registry as the JSON ones
    # (`BINARY_GET_ROUTES`). Only the plumbing is here: these write bytes to the
    # socket instead of returning `(status, dict)`, so their handlers cannot
    # live in a module with no server dependency — but the *surface* is declared
    # beside the other routes rather than hidden in this dispatcher.
    for route in BINARY_GET_ROUTES:
        if not route.matches(p):
            continue
        handler = BINARY_GET_HANDLERS.get(route.name)
        if handler is None:
            # A declared route with no handler. This used to be an `if
            # route.name == "pdf": ... else: export`, so a third entry in
            # `BINARY_GET_ROUTES` was served as an export of the whole library.
            _respond(request, 404, {"error": "not found"}, security)
            return
        qs_raw = parse_qs(urlsplit(request.path).query)
        handler(
            request,
            config_path,
            home_dir,
            security,
            suffix=p[len(route.path):],
            query=qs_raw,
        )
        return

    status, body = process_get_request(request.path, config_path, home_dir)
    _respond(request, status, body, security)


def _handle_post(
    request: BaseHTTPRequestHandler,
    config_path: str,
    home_dir: str,
    security: HttpSecurityConfig,
) -> None:
    error = _request_gate(request, "POST", security)
    if error is not None:
        _respond(request, error[0], {"error": error[1]}, security)
        return
    # Only count an accepted request against the idle-stop timer (mirrors GET),
    # so a rejected POST can't keep the auto-stop server alive.
    idle_state = getattr(request, "_idle_state", None)
    if idle_state is not None:
        idle_state["_last_request"] = time.monotonic()
    length_result = validated_content_length(
        request.headers.get("Content-Length"),
        max_body_bytes=security["max_body_bytes"],
        transfer_encoding=request.headers.get("Transfer-Encoding"),
    )
    if isinstance(length_result, tuple):
        _respond(request, length_result[0], {"error": length_result[1]}, security)
        return
    length = length_result
    # A truncated body leaves this read blocking until the socket times out,
    # and `TimeoutError` then fell through to the generic handler as a 500
    # after 30 seconds. 408 is what a request the client did not finish is.
    try:
        raw = request.rfile.read(length) if length > 0 else b""
    except TimeoutError:
        _respond(request, 408, {"error": "request body incomplete"}, security)
        return
    try:
        parsed_path = urlsplit(request.path)
    except ValueError:
        _respond(request, 400, {"error": "invalid request target"}, security)
        return
    if parsed_path.path == "/attach-pdf-raw":
        query = parse_qs(parsed_path.query)
        body = {
            "request_id": query.get("request_id", [None])[0],
            # Header only. `pdf_acquisition_plan._attach_url` deliberately
            # keeps the token out of the URL and returns it in a header,
            # because a URL lands in access logs, `Referer`, and shell history —
            # so accepting it from the query string here reintroduced exactly
            # the leak the other half was written to avoid.
            "attach_token": request.headers.get("X-Pzi-Attach-Token"),
            "citekey": query.get("citekey", [None])[0],
            "bib": query.get("bib", [None])[0],
            "source_url": query.get("source_url", [None])[0],
            # Which planned candidate the fetch began from, when the bytes ended
            # up elsewhere — a publisher redirecting to a CDN is the normal
            # case, and the plan is still what authorises the attach.
            "origin_candidate": query.get("origin_candidate", [None])[0],
            "pdf_bytes": raw,
        }
        status, response_body = process_post_request(
            parsed_path.path,
            body,
            config_path,
            home_dir,
            attach_session_store=getattr(request, "_attach_session_store", None),
        )
        _respond(request, status, response_body, security)
        return
    try:
        body: Any = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        _respond(request, 400, {"error": "invalid JSON body"}, security)
        return
    except RecursionError:
        # A deeply nested body exhausts the parser's stack. That is the client's
        # input being unacceptable, not the server failing — it used to reach
        # the catch-all and answer 500.
        _respond(request, 400, {"error": "JSON body is nested too deeply"}, security)
        return

    status, response_body = process_post_request(
        request.path, body, config_path, home_dir,
        browser_manager=getattr(request, "_browser_session_manager", None),
        attach_session_store=getattr(request, "_attach_session_store", None),
    )
    _respond(request, status, response_body, security)


def _send_cors_headers(request: BaseHTTPRequestHandler, security: HttpSecurityConfig) -> None:
    # `headers` is unset when the request line itself failed to parse, and
    # `send_error` answers that case. There is no Origin to echo then; the
    # static headers still go out.
    headers = getattr(request, "headers", None)
    origin = headers.get("Origin") if headers is not None else None
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
) -> None:
    body = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    request._response_started = True  # type: ignore[attr-defined]
    request.send_response(status)
    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(body)))
    request.send_header("X-Content-Type-Options", "nosniff")
    _send_cors_headers(request, security)
    request.end_headers()
    try:
        request.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        return


def run_server(
    *,
    config_path: str,
    home_dir: str,
    host: str,
    port: int,
    server_class: type[HTTPServer] = ThreadingHTTPServer,
    security: HttpSecurityConfig | None = None,
    idle_minutes: int | None = None,
    browser_profile_path: str | None = None,
    browser_engine: str = "chromium",
    log_requests_to: TextIO | None = None,
) -> None:
    security_config = security or build_http_security_config(listen_host=host)
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
        log_requests_to=log_requests_to,
    )
    idle_state: dict[str, float] | None = None
    if idle_minutes is not None:
        idle_state = {"_last_request": time.monotonic()}
        handler._idle_state = idle_state  # type: ignore[attr-defined]

    server = server_class((host, port), handler)
    server.socket.settimeout(30)

    def _shutdown() -> None:
        browser_manager.close()

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
    *,
    poll_seconds: float = IDLE_POLL_SECONDS,
    start_thread: bool = True,
) -> Callable[[], None]:
    """Shut the server down once it has been idle for *idle_minutes*.

    Returns the monitor loop so it can be driven directly in a test; normally it
    is started on a daemon thread. *poll_seconds* is how often idleness is
    checked, so the actual shutdown lands up to one poll late.
    """
    import threading

    def _monitor() -> None:
        while True:
            time.sleep(poll_seconds)
            elapsed = time.monotonic() - idle_state["_last_request"]
            if elapsed > idle_minutes * 60:
                server.shutdown()
                if on_shutdown is not None:
                    on_shutdown()
                return

    if start_thread:
        t = threading.Thread(target=_monitor, daemon=True)
        t.start()
    return _monitor
