"""`pzi server --log-requests` — opt-in, to stderr, without the query string.

`pzi server` wrote nothing per request, so a capture that failed inside the
extension left no record of what was sent. This is off by default: request
URLs name what you read, and that should not accumulate in a scrollback or a
journald ring buffer unless you asked.
"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from io import StringIO
from pathlib import Path

import pytest

from pzi.http_api import build_handler_class
from pzi.http_security import build_http_security_config


@pytest.fixture
def served(tmp_path: Path):
    """A real server on a real socket, with the log sink under test."""
    bib = tmp_path / "ml.bib"
    bib.write_text("@article{a2020,\n  title = {A},\n}\n", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n', encoding="utf-8"
    )

    def _serve(log_to):
        handler = build_handler_class(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            security=build_http_security_config(auth_token=None, listen_host="127.0.0.1"),
            log_requests_to=log_to,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{server.server_port}"

    return _serve


def test_requests_are_logged_when_asked(served) -> None:
    log = StringIO()
    server, base = served(log)
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()

    line = log.getvalue().strip()
    assert line, "nothing was logged"
    assert "GET" in line
    assert "/health" in line
    assert "200" in line
    assert "ms" in line


def test_nothing_is_logged_by_default(served) -> None:
    server, base = served(None)
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5):
            pass
    finally:
        server.shutdown()
        server.server_close()
    # No sink, no output, and no crash on the path that skips the timing wrapper.


def test_the_query_string_is_not_logged(served) -> None:
    """It carries `bib=`, `citekey=` and historically an attach token.

    A logged URL names the paper you were reading; the path alone is enough to
    debug a route.
    """
    log = StringIO()
    server, base = served(log)
    try:
        # Whether the route accepts these parameters is beside the point: the
        # request is logged either way, and that is what must not carry them.
        with contextlib.suppress(urllib.error.HTTPError):
            with urllib.request.urlopen(
                f"{base}/search?query=a-secret-search-term&bib=ml", timeout=5
            ):
                pass
    finally:
        server.shutdown()
        server.server_close()

    logged = log.getvalue()
    assert "/search" in logged
    assert "a-secret-search-term" not in logged
    assert "?" not in logged


def test_a_failed_request_is_logged_with_its_status(served) -> None:
    """Logging wraps the 500 guard rather than replacing it, so a handler that
    raises is logged with the status the guard sent — not dropped."""
    log = StringIO()
    server, base = served(log)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{base}/no-such-route", timeout=5)
        assert caught.value.code == 404
    finally:
        server.shutdown()
        server.server_close()

    logged = log.getvalue()
    assert "/no-such-route" in logged
    assert "404" in logged


def test_the_flag_is_registered_on_the_server_command() -> None:
    """A unit test cannot catch an unregistered flag; the parser can."""
    from pzi.cli_parser import build_parser

    args = build_parser().parse_args(["server", "--log-requests"])
    assert args.log_requests is True
    assert build_parser().parse_args(["server"]).log_requests is False


def test_json_routes_still_answer_json_with_logging_on(served) -> None:
    log = StringIO()
    server, base = served(log)
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
            payload = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
    assert isinstance(payload, dict)


def test_a_malformed_target_is_logged_rather_than_crashing_the_logger() -> None:
    """`GET http://[evil/health` is a legal absolute-form target that makes
    `urlsplit` raise — a case this server has met before (see `_handle_get`'s
    own guard). The logger must not be the thing that turns it into a 500."""
    from pzi.http_api import _log_request

    class _Malformed:
        command = "GET"
        path = "http://[evil/health"

    sink = StringIO()
    _log_request(_Malformed(), 0.0, sink)  # type: ignore[arg-type]

    logged = sink.getvalue()
    assert "GET" in logged
    assert "(unparseable)" in logged


def test_a_request_that_never_responded_logs_a_dash() -> None:
    """Worth seeing rather than hiding: no status means nothing was sent."""
    from pzi.http_api import _log_request

    class _Silent:
        command = "POST"
        path = "/capture"

    sink = StringIO()
    _log_request(_Silent(), 0.0, sink)  # type: ignore[arg-type]
    assert "/capture -" in sink.getvalue()


def test_a_request_that_reaches_no_handler_is_still_logged(served) -> None:
    """`PUT` and an unparseable request line answer via `send_error`.

    That path never enters the `do_*` wrapper that does the timing, so these
    were the two requests `--log-requests` could not show you — precisely the
    ones an operator is trying to see when something is sending this server
    what it does not understand.
    """
    import socket

    log = StringIO()
    server, base = served(log)
    port = server.server_port
    try:
        for payload in (
            b"PUT /capture HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n",
            b"GET / HTTP/x\r\nHost: 127.0.0.1\r\n\r\n",
        ):
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            try:
                sock.sendall(payload)
                while sock.recv(4096):
                    pass
            finally:
                sock.close()
    finally:
        server.shutdown()
        server.server_close()

    lines = log.getvalue().strip().splitlines()
    assert len(lines) == 2, lines
    assert lines[0].startswith("PUT /capture 501 "), lines[0]
    # No method and no path survived the parse failure; both log as `-` rather
    # than the line going missing.
    assert lines[1].startswith("- - 400 "), lines[1]
