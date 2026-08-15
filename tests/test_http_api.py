import http.client
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from pzi.add_service import add_record_to_bib
from pzi.http_api import (
    CONNECTION_READ_TIMEOUT_SECONDS,
    build_handler_class,
    build_http_security_config,
    origin_allowed,
    request_security_error,
    validated_content_length,
)


@pytest.fixture(autouse=True)
def _block_external_http(monkeypatch):
    """Keep these real-server tests hermetic and fast.

    Every metadata/PDF provider routes outbound HTTP through
    ``pzi.fetch_helpers``; make those calls fail instantly (HTTPError is not
    retried, so no backoff sleeps) so captures fall back to the page metadata
    in the request without touching the real internet or blocking under load.
    The in-process test client uses ``urllib.request`` directly and is
    unaffected, and the translation-server client targets a dead local port
    (instant connection refusal).
    """
    import pzi.fetch_helpers as fetch_helpers

    def _blocked(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://blocked.invalid", 503, "external network blocked in tests", {}, None
        )

    monkeypatch.setattr(fetch_helpers, "safe_urlopen", _blocked)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve_once(
    config_path: Path,
    home_dir: Path,
    *,
    token: str | None = None,
    max_body_bytes: int = 5 * 1024 * 1024,
) -> tuple[int, threading.Thread, HTTPServer]:
    port = _free_port()
    security = build_http_security_config(auth_token=token, max_body_bytes=max_body_bytes)
    handler = build_handler_class(
        config_path=str(config_path), home_dir=str(home_dir), security=security
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port, thread, server


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
        },
        bib_selector=None,
        dry_run=False,
    )
    return config_path, bib_path


def test_handler_class_sets_per_connection_read_timeout(tmp_path: Path) -> None:
    # Regression: `server.socket.settimeout()` only bounds accept() on the
    # listening socket, not reads on sockets already accepted — a slowloris
    # client trickling bytes (or none) could hold a thread open forever
    # without this. `StreamRequestHandler.setup()` applies the `timeout`
    # class attribute to each accepted connection via
    # `self.connection.settimeout(...)`.
    handler = build_handler_class(config_path=str(tmp_path / "c.toml"), home_dir=str(tmp_path))
    assert handler.timeout == CONNECTION_READ_TIMEOUT_SECONDS
    assert CONNECTION_READ_TIMEOUT_SECONDS > 0


def test_get_bibs_returns_bib_list(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/bibs", timeout=10
        )
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert payload["status"] == "ok"
    assert payload["bibs"][0]["name"] == "ml"


def test_get_health_includes_config_status(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=10
        )
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert payload["config_ok"] is True


def test_get_health_reports_the_server_version(tmp_path: Path) -> None:
    """Item 425: the extension compares this against its own `version_name`.

    Asserted against `package_version()` rather than a literal, so cutting a
    release does not break the test — what must hold is that the field is there
    and says what this server actually is. Without it the extension cannot tell
    a version mismatch from a healthy server, which is the whole handshake.
    """
    from pzi import package_version

    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=10
        )
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert payload["version"] == package_version()


def test_get_rejects_dns_rebinding_host_header(tmp_path: Path) -> None:
    # Loopback bind (default): a request whose Host header names a foreign
    # domain — as a DNS-rebinding page pointing its own domain at 127.0.0.1
    # would send — must be rejected even though it reaches us on 127.0.0.1.
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.putrequest("GET", "/health", skip_host=True)
        conn.putheader("Host", "attacker.com")
        conn.endheaders()
        response = conn.getresponse()
        status = response.status
        response.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
    assert status == 403


def test_post_capture_inserts_new_entry_dry_run(tmp_path: Path) -> None:
    config_path, bib_path = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        # Valid identifier + inline page metadata so the capture resolves
        # offline (external HTTP is blocked in these tests) via the manual-entry
        # fallback, without relying on a live translation-server.
        body = json.dumps(
            {
                "url": "https://example.com/new-paper",
                "page_title": "A New Paper",
                "doi": "10.1234/new",
                "dry_run": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=10)
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True


def test_post_capture_missing_url_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_unknown_path_returns_404(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/nope", timeout=10
            )
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_post_attach_pdf_bytes_updates_entry(tmp_path: Path) -> None:
    import base64

    config_path, bib_path = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps(
            {
                "citekey": "smith2024graph",
                "pdf_base64": base64.b64encode(b"%PDF-1.4 browser").decode("ascii"),
                "source_url": "https://example.com/browser.pdf",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=10)
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert payload["status"] == "ok"
    text = bib_path.read_text()
    assert "file = {" in text
    assert "pzi-pdf-url = {https://example.com/browser.pdf}" in text


def test_options_request_returns_204_with_cors_headers(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            method="OPTIONS",
        )
        response = urllib.request.urlopen(request, timeout=10)
        assert response.status == 204
        assert response.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1"
        assert "POST" in response.headers.get("Access-Control-Allow-Methods", "")
        assert "X-Pzi-Attach-Token" in response.headers.get(
            "Access-Control-Allow-Headers", ""
        )
    finally:
        server.shutdown()
        server.server_close()


def test_get_bibs_includes_cors_headers(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/bibs", timeout=10
        )
        assert response.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()


def test_get_pdf_includes_cors_headers(tmp_path: Path) -> None:
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    pdf_path = papers_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        f"""
@article{{smith2024graph,
  title = {{Graph Parsers}},
  file = {{{pdf_path}}}
}}
""".strip()
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
papers_dir = "{papers_dir}"
default = true
""".strip()
    )
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/pdf/smith2024graph",
            headers={"Origin": "chrome-extension://abc"},
        )
        response = urllib.request.urlopen(request, timeout=10)
        assert response.status == 200
        assert response.headers.get("Access-Control-Allow-Origin") == "chrome-extension://abc"
    finally:
        server.shutdown()
        server.server_close()


def test_get_pdf_rejects_path_outside_papers_dir(tmp_path: Path) -> None:
    outside_pdf = tmp_path / "secret.pdf"
    outside_pdf.write_bytes(b"%PDF-1.4\nsecret\n")
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        f"""
@article{{smith2024graph,
  title = {{Graph Parsers}},
  file = {{{outside_pdf}}}
}}
""".strip()
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
papers_dir = "{papers_dir}"
default = true
""".strip()
    )
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/pdf/smith2024graph", timeout=10
            )
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_get_export_raw_returns_export_content_with_content_type(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        response = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/export/raw?format=bibtex", timeout=10
        )
        body = response.read()
    finally:
        server.shutdown()
        server.server_close()

    assert response.status == 200
    assert response.headers.get("Content-Type") == "application/x-bibtex"
    assert "inline" in response.headers.get("Content-Disposition", "")
    assert b"smith2024graph" in body


def test_post_capture_accepts_page_metadata_overrides(tmp_path: Path) -> None:
    config_path, bib_path = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps(
            {
                "url": "https://example.com/browser-page",
                "page_title": "Browser Metadata Title",
                "doi": "10.1234/browser-meta",
                "canonical_url": "https://example.com/browser-page",
                "source_url": "https://example.com/browser-page",
                "abstract_url": "https://example.com/browser-page",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        response = urllib.request.urlopen(request, timeout=10)
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert payload["status"] == "ok"
    text = bib_path.read_text()
    assert "Browser Metadata Title" in text
    assert "10.1234/browser-meta" in text


def test_origin_allowed_accepts_local_and_extension_origins() -> None:
    security = build_http_security_config()

    assert origin_allowed(None, security["allowed_origins"])
    assert origin_allowed("http://127.0.0.1", security["allowed_origins"])
    assert origin_allowed("chrome-extension://abc123", security["allowed_origins"])
    assert not origin_allowed("https://evil.example", security["allowed_origins"])


def test_request_security_error_requires_token_when_configured() -> None:
    security = build_http_security_config(auth_token="secret")

    assert request_security_error(method="GET", headers={}, security=security) == (
        401,
        "invalid API token",
    )
    assert request_security_error(
        method="GET", headers={"X-Pzi-Token": "secret"}, security=security
    ) is None
    assert request_security_error(
        method="GET",
        headers={"Origin": "https://evil.example", "X-Pzi-Token": "secret"},
        security=security,
    ) == (403, "origin not allowed")


def test_validated_content_length_rejects_invalid_and_large_values() -> None:
    assert validated_content_length(None, max_body_bytes=10) == 0
    assert validated_content_length("5", max_body_bytes=10) == 5
    assert validated_content_length("bad", max_body_bytes=10) == (400, "invalid Content-Length")
    assert validated_content_length("11", max_body_bytes=10) == (413, "request body too large")


def test_get_bibs_requires_configured_token(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path, token="secret")
    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/bibs", timeout=10)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/bibs",
            headers={"X-Pzi-Token": "secret"},
        )
        response = urllib.request.urlopen(request, timeout=10)
        assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_post_rejects_oversized_body_before_read(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path, max_body_bytes=2)
    try:
        body = json.dumps({"url": "10.1/new"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 413
    finally:
        server.shutdown()
        server.server_close()


def test_unexpected_service_exception_returns_500_not_a_dropped_connection(
    tmp_path: Path, monkeypatch
) -> None:
    """A dropped connection is indistinguishable from the server being down.

    `BaseHTTPRequestHandler.handle_one_request` catches only `TimeoutError`, so
    any other exception closed the socket having sent zero bytes — the client
    sees `RemoteDisconnected` either way and cannot tell the difference.
    """
    import pzi.http_api

    def boom(*_args, **_kwargs):
        raise RuntimeError("service exploded with /secret/path in the message")

    monkeypatch.setattr(pzi.http_api, "process_get_request", boom)

    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/bibs")
        response = conn.getresponse()
        status = response.status
        payload = json.loads(response.read())
        conn.close()
    finally:
        server.shutdown()
        server.server_close()

    assert status == 500
    # The failure is reported, but internals are not handed to the client.
    assert payload == {"error": "internal server error"}
    assert "secret" not in json.dumps(payload)


def test_pdf_not_found_returns_a_json_body_not_an_empty_response(
    tmp_path: Path,
) -> None:
    """The planner already built a JSON error; the route discarded it.

    Bare `send_response` also skipped the CORS headers, so a cross-origin
    extension could not read even the status.
    """
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/pdf/nosuch2024", headers={"Origin": "http://localhost"})
        response = conn.getresponse()
        status = response.status
        cors = response.headers.get("Access-Control-Allow-Origin")
        payload = json.loads(response.read())
        conn.close()
    finally:
        server.shutdown()
        server.server_close()

    assert status == 404
    assert "nosuch2024" in payload["error"]
    assert cors is not None


def test_pdf_route_decodes_a_percent_encoded_citekey(tmp_path: Path) -> None:
    """The extension builds these with `encodeURIComponent`, which escapes `:`.

    Nothing decoded the segment, so any citekey containing a colon 404'd even
    when its PDF was present.
    """
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    pdf = papers / "colon.pdf"
    pdf.write_bytes(b"%PDF-1.4\nbody\n")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        f"@article{{smith:2024, title = {{Colon Key}}, file = {{{pdf}}}}}\n"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname="ml"\npath="{bib_path}"\n'
        f'papers_dir="{papers}"\ndefault=true\n'
    )

    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/pdf/smith%3A2024")
        response = conn.getresponse()
        status = response.status
        body = response.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()

    assert status == 200
    assert body.startswith(b"%PDF-")


# === --stop-after idle shutdown (previously untested) ==================


class _FakeServer:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _run_monitor_once(idle_state, idle_minutes, *, clock, monkeypatch):
    """Drive one poll of the idle monitor with a scripted clock, no sleeping."""
    import pzi.http_api

    server = _FakeServer()
    stopped: list[str] = []
    monkeypatch.setattr(pzi.http_api.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pzi.http_api.time, "monotonic", clock)
    monitor = pzi.http_api._start_idle_monitor(
        server, idle_state, idle_minutes, lambda: stopped.append("x"),
        poll_seconds=0, start_thread=False,
    )
    monitor()
    return server, stopped


def test_idle_monitor_shuts_down_once_the_window_has_passed(monkeypatch) -> None:
    """The whole `--stop-after` path had no test at all."""
    # Last request at t=0; the clock reads 10 minutes later, window is 5.
    server, stopped = _run_monitor_once(
        {"_last_request": 0.0}, 5, clock=lambda: 600.0, monkeypatch=monkeypatch,
    )

    assert server.shutdown_calls == 1
    assert stopped == ["x"]


def test_idle_monitor_keeps_running_while_requests_arrive(monkeypatch) -> None:
    """A request inside the window resets the timer, so the loop must not stop.

    The monitor loops forever until the window passes, so the clock advances
    while the request timestamp keeps up, and stops the loop by raising.
    """
    import pzi.http_api

    idle_state = {"_last_request": 0.0}
    ticks = iter([60.0, 120.0, 180.0])

    def clock() -> float:
        now = next(ticks)
        # Traffic keeps arriving: the timestamp tracks the clock.
        idle_state["_last_request"] = now
        return now

    server = _FakeServer()
    stopped: list[str] = []
    monkeypatch.setattr(pzi.http_api.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pzi.http_api.time, "monotonic", clock)
    monitor = pzi.http_api._start_idle_monitor(
        server, idle_state, 5, lambda: stopped.append("x"),
        poll_seconds=0, start_thread=False,
    )

    with pytest.raises(StopIteration):
        monitor()  # runs out of scripted ticks rather than shutting down

    assert server.shutdown_calls == 0
    assert stopped == []


def test_rejected_requests_do_not_keep_an_auto_stop_server_alive(
    tmp_path: Path,
) -> None:
    """The idle timer is refreshed only after auth and rate-limit checks pass.

    Otherwise an unauthenticated caller could hold a `--stop-after` server open
    indefinitely without ever being allowed to do anything.
    """
    config_path, _ = _seed(tmp_path)
    security = build_http_security_config(auth_token="sekrit")
    handler = build_handler_class(
        config_path=str(config_path), home_dir=str(tmp_path), security=security
    )
    idle_state = {"_last_request": 0.0}
    handler._idle_state = idle_state  # type: ignore[attr-defined]

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/bibs")  # no token -> 401
        assert conn.getresponse().status == 401
        conn.close()
    finally:
        server.shutdown()
        server.server_close()

    assert idle_state["_last_request"] == 0.0, "a rejected request refreshed the timer"
