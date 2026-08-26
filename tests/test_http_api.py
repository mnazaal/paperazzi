import http.client
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from pzi.add_service import add_record_with_bib
from pzi.config import BibResolutionFailure, load_bib_target
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
    browser_manager: object | None = None,
) -> tuple[int, threading.Thread, HTTPServer]:
    port = _free_port()
    security = build_http_security_config(auth_token=token, max_body_bytes=max_body_bytes)
    handler = build_handler_class(
        config_path=str(config_path),
        home_dir=str(home_dir),
        security=security,
        browser_manager=browser_manager,
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
    # Seeds through the live write path (`add_record_with_bib`), inlining what
    # the now-deleted single-record capture wrapper used to.
    resolved = load_bib_target(
        config_path=str(config_path), home_dir=str(tmp_path), bib_selector=None,
    )
    assert not isinstance(resolved, BibResolutionFailure)
    _config, bib = resolved
    add_record_with_bib(
        bib=bib,
        record={
            "citekey": "smith2024graph",
            "title": "Graph Parsers",
            "doi": "10.1/foo",
        },
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


def test_post_attach_pdf_bytes_decodes_the_payload_only_once(
    tmp_path: Path, monkeypatch
) -> None:
    """C6: the sessionless attach path used to decode the same base64 PDF
    twice — once for the size check, once inside `attach_pdf_bytes` — for one
    request. It now decodes once and passes bytes down."""
    import base64

    real_b64decode = base64.b64decode
    calls: list[int] = []

    def _counting_b64decode(*args, **kwargs):
        calls.append(1)
        return real_b64decode(*args, **kwargs)

    monkeypatch.setattr(base64, "b64decode", _counting_b64decode)

    config_path, bib_path = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps(
            {
                "citekey": "smith2024graph",
                "pdf_base64": base64.b64encode(b"%PDF-1.4 once").decode("ascii"),
                "source_url": "https://example.com/once.pdf",
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
    # Before the fix this was 2: once for the size check, once inside
    # `attach_pdf_bytes`. The request's own `pdf_base64` is built with
    # `b64encode`, which this patch does not touch.
    assert len(calls) == 1


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


def test_an_exact_binary_route_does_not_match_a_longer_path(tmp_path: Path) -> None:
    """`/export/raw` is exact; `/pdf/` is a prefix. The dispatcher must obey.

    `BinaryGetRoute.is_prefix` is what says which, and
    `test_each_binary_route_declares_how_it_matches` covers the dataclass —
    mutating `matches` to `startswith` does fail it. What nothing covered is
    the dispatcher *using* `matches`: rewriting `http_api`'s loop to compare
    with `startswith` directly, ignoring `is_prefix`, made `/export/rawXYZ` a
    200 with the whole suite green, because no test ever issued that request.

    So this one goes over the wire. `/pdf/<citekey>` is asserted elsewhere; the
    exact half is the one that had no end-to-end check.
    """
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/export/rawXYZ?format=bibtex", timeout=10
            )
            raise AssertionError("expected HTTPError — /export/raw is not a prefix")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


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


# ---------------------------------------------------------------------------
# Requests that never reach a `do_*` handler
# ---------------------------------------------------------------------------


def _raw_exchange(port: int, payload: bytes) -> bytes:
    """Send a hand-built request and read the whole response.

    `http.client` will not send what these tests need — a request line with a
    bad version, or two `Host` headers — so they go down a plain socket.
    """
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        sock.sendall(payload)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    return b"".join(chunks)


def _json_body(raw: bytes) -> dict:
    """The JSON document from a raw response, headers or not.

    A request line the parser rejected leaves `request_version` at HTTP/0.9,
    where the stdlib suppresses the status line and headers entirely — so the
    response is the body alone.
    """
    head, separator, body = raw.partition(b"\r\n\r\n")
    return json.loads(body if separator else head)


def test_an_unsupported_method_answers_json_with_the_security_headers(tmp_path: Path) -> None:
    """`PUT` reaches no `do_*` method, so it used to get the stdlib HTML page.

    No CORS headers (a cross-origin caller could not read it), no
    `X-Content-Type-Options`, and a `Server` header naming the exact CPython
    patch version — all of it before any token was checked.
    """
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        raw = _raw_exchange(
            port,
            b"PUT /capture HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Origin: http://127.0.0.1\r\nContent-Length: 0\r\n\r\n",
        )
    finally:
        server.shutdown()
        server.server_close()

    head, _, _body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 501 ")
    assert b"Content-Type: application/json" in head
    assert b"X-Content-Type-Options: nosniff" in head
    assert b"Access-Control-Allow-Origin: http://127.0.0.1" in head
    assert b"<html" not in raw.lower()
    assert isinstance(_json_body(raw)["error"], str)


def test_a_head_request_gets_the_headers_and_no_body(tmp_path: Path) -> None:
    """A body on a HEAD response is a protocol violation, JSON or not."""
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        raw = _raw_exchange(port, b"HEAD /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    finally:
        server.shutdown()
        server.server_close()

    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.0 501 ")
    assert b"Content-Type: application/json" in head
    assert body == b""


def test_a_malformed_request_line_answers_json_not_an_html_page(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        raw = _raw_exchange(port, b"GET / HTTP/x\r\nHost: 127.0.0.1\r\n\r\n")
    finally:
        server.shutdown()
        server.server_close()

    assert b"<html" not in raw.lower()
    assert isinstance(_json_body(raw)["error"], str)


def test_no_response_discloses_the_interpreter_version(tmp_path: Path) -> None:
    """`Server: pzi/0.1 Python/3.12.3` named the exact patch level, pre-auth.

    Both halves were wrong: the version was stale (the package is past 0.1) and
    the interpreter is nobody's business. Checked on a served route and on the
    `send_error` path, because they build the header the same way and only one
    of them was ever looked at.
    """
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        served = _raw_exchange(port, b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        errored = _raw_exchange(port, b"PUT /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    finally:
        server.shutdown()
        server.server_close()

    for raw in (served, errored):
        head = raw.partition(b"\r\n\r\n")[0]
        assert b"Python/" not in head, head
        # `version_string()` is `server_version + " " + sys_version`, so an
        # empty `sys_version` leaves one trailing space that every HTTP parser
        # strips. What matters is that nothing after `pzi` is a version.
        server = next(
            line for line in head.split(b"\r\n") if line.startswith(b"Server:")
        )
        assert server.partition(b":")[2].strip() == b"pzi", server


def test_a_second_host_header_is_refused_in_either_order(tmp_path: Path) -> None:
    """`dict(headers.items())` keeps the last value, so order decided the answer.

    `Host: evil.com` then `Host: 127.0.0.1` passed the rebinding guard; the
    reverse was refused. RFC 7230 section 5.4 says a message with more than one
    `Host` is rejected, which is also the only answer that does not depend on
    which copy a proxy happens to put first.
    """
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        for first, second in ((b"evil.example", b"127.0.0.1"), (b"127.0.0.1", b"evil.example")):
            for method in (b"GET", b"POST", b"OPTIONS"):
                raw = _raw_exchange(
                    port,
                    method + b" /bibs HTTP/1.1\r\nHost: " + first
                    + b"\r\nHost: " + second + b"\r\nContent-Length: 0\r\n\r\n",
                )
                assert raw.startswith(b"HTTP/1.0 400 "), (method, first, second, raw[:80])
                assert "Host" in _json_body(raw)["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_a_binary_route_with_no_handler_is_not_served_as_an_export(
    tmp_path: Path, monkeypatch
) -> None:
    """A third `BINARY_GET_ROUTES` entry used to be served as an export.

    The dispatcher branched `if route.name == "pdf": ... else: export`, so any
    name that was not `pdf` got the export handler — while `BinaryGetRoute`
    documented `name` as a key into a handler table `http_api` did not have.
    """
    import pzi.http_api as http_api
    from pzi.http_get_routes import BinaryGetRoute

    monkeypatch.setattr(
        http_api,
        "BINARY_GET_ROUTES",
        (*http_api.BINARY_GET_ROUTES, BinaryGetRoute("/not-wired/", "not_wired", is_prefix=True)),
    )
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        raw = _raw_exchange(
            port, b"GET /not-wired/x HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        )
    finally:
        server.shutdown()
        server.server_close()

    assert raw.startswith(b"HTTP/1.0 404 "), raw[:120]
    assert b"@article" not in raw
    assert _json_body(raw)["error"] == "not found"


# === G1: a crashed browser session must not answer /browser/discover or
# /browser/download with 200 the same way "genuinely no PDF" does. ===


class _FakeBrowserManager:
    """Stands in for BrowserSessionManager; each method takes the same
    `errors=` list `browser_pdf_hook.discover_pdf_url`/`download_pdf` do."""

    def __init__(self, *, crashed: bool) -> None:
        self._crashed = crashed

    def discover_pdf_url(self, page_url: str, *, errors: list[str] | None = None) -> str | None:
        if self._crashed and errors is not None:
            errors.append("browser session: browser session is closed")
        return None

    def download_pdf_bytes(self, pdf_url: str, *, errors: list[str] | None = None) -> bytes | None:
        if self._crashed and errors is not None:
            errors.append("browser session: browser session is closed")
        return None


def _post_json(port: int, path: str, body: dict) -> urllib.request.Request:
    return urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def test_browser_discover_no_pdf_is_200(tmp_path: Path) -> None:
    """A browser stage that genuinely finds nothing is not an error."""
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(
        config_path, tmp_path, browser_manager=_FakeBrowserManager(crashed=False)
    )
    try:
        response = urllib.request.urlopen(
            _post_json(port, "/browser/discover", {"page_url": "https://example.test/a"}),
            timeout=10,
        )
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert response.status == 200
    assert payload == {"pdf_url": None}


def test_browser_discover_crashed_session_is_503(tmp_path: Path) -> None:
    """G1: a crashed session must not be indistinguishable from "no PDF"."""
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(
        config_path, tmp_path, browser_manager=_FakeBrowserManager(crashed=True)
    )
    try:
        try:
            urllib.request.urlopen(
                _post_json(port, "/browser/discover", {"page_url": "https://example.test/a"}),
                timeout=10,
            )
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert "browser session" in json.loads(exc.read())["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_browser_download_no_pdf_is_200(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(
        config_path, tmp_path, browser_manager=_FakeBrowserManager(crashed=False)
    )
    try:
        response = urllib.request.urlopen(
            _post_json(
                port, "/browser/download", {"pdf_url": "https://example.test/a.pdf"}
            ),
            timeout=10,
        )
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert response.status == 200
    assert payload == {"pdf_base64": None}


def test_browser_download_crashed_session_is_503(tmp_path: Path) -> None:
    """Sibling of test_browser_discover_crashed_session_is_503 (G1)."""
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(
        config_path, tmp_path, browser_manager=_FakeBrowserManager(crashed=True)
    )
    try:
        try:
            urllib.request.urlopen(
                _post_json(
                    port, "/browser/download", {"pdf_url": "https://example.test/a.pdf"}
                ),
                timeout=10,
            )
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert "browser session" in json.loads(exc.read())["error"]
    finally:
        server.shutdown()
        server.server_close()
