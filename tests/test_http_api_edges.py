"""Edge-case coverage tests for pzi.http_api — targets specific uncovered lines."""

from __future__ import annotations

import json
import socket
import threading
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, ANY

import urllib.error
import urllib.request

from pzi.add_service import add_record_to_bib
from pzi.http_api import (
    _pdf_url_candidates_from_body,
    _record_overrides_from_capture_body,
    build_handler_class,
    build_http_security_config,
    origin_allowed,
    request_security_error,
    run_server,
    validated_content_length,
)


# ---------------------------------------------------------------------------
# helpers (copied pattern from test_http_api.py to avoid modifying that file)
# ---------------------------------------------------------------------------

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
    allowed_origins: tuple[str, ...] | None = None,
) -> tuple[int, threading.Thread, HTTPServer]:
    port = _free_port()
    security = build_http_security_config(
        auth_token=token,
        max_body_bytes=max_body_bytes,
        allowed_origins=allowed_origins,
    )
    handler = build_handler_class(
        config_path=str(config_path), home_dir=str(home_dir), security=security
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return port, thread, server


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal config + bib with one entry; return (config_path, bib_path)."""
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


# ---------------------------------------------------------------------------
# line 82-83  – _handle_options error path (bad origin → 403)
# line 253→256 – _send_cors_headers else branch (origin not allowed, skips ACAO)
# ---------------------------------------------------------------------------

def test_options_bad_origin_returns_403(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            method="OPTIONS",
            headers={"Origin": "https://evil.example"},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            body = json.loads(exc.read().decode("utf-8"))
            assert "origin" in body.get("error", "")
            # Verify CORS headers still sent but ACAO/Vary skipped
            acao = exc.headers.get("Access-Control-Allow-Origin", "")
            vary = exc.headers.get("Vary", "")
            assert acao == "" or acao != "https://evil.example"
            assert "Origin" not in vary
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 122-123 – _handle_post security error (POST without token → 401)
# ---------------------------------------------------------------------------

def test_post_requires_token_returns_401(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path, token="secret")
    try:
        body = json.dumps({"url": "10.1/new"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 401")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 134-136 – _handle_post invalid JSON body → 400
# ---------------------------------------------------------------------------

def test_post_invalid_json_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=b"not { valid json !!!",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode("utf-8"))
            assert "invalid JSON" in body.get("error", "")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 145 – _handle_post unknown path → 404
# ---------------------------------------------------------------------------

def test_post_unknown_path_returns_404(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({"foo": "bar"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/nonexistent",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 156-157 – _handle_capture body not a dict → 400
# ---------------------------------------------------------------------------

def test_post_capture_non_dict_body_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps([1, 2, 3]).encode("utf-8")  # valid JSON but a list
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            resp_body = json.loads(exc.read().decode("utf-8"))
            assert "must be a JSON object" in resp_body.get("error", "")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 180 – _record_overrides_from_capture_body  tags list comprehension
# ---------------------------------------------------------------------------

def test_record_overrides_filters_tags() -> None:
    body: dict = {
        "tags": ["ml", "graph", "", "  ", 42, None],
        "page_title": "Some Title",
    }
    overrides = _record_overrides_from_capture_body(body)
    assert overrides.get("tags") == ["ml", "graph"]
    assert overrides.get("title") == "Some Title"


def test_record_overrides_no_tags_key() -> None:
    overrides = _record_overrides_from_capture_body({"url": "x"})
    assert "tags" not in overrides


# ---------------------------------------------------------------------------
# line 198 – _pdf_url_candidates_from_body list comprehension return
# ---------------------------------------------------------------------------

def test_pdf_url_candidates_from_body_filters() -> None:
    result = _pdf_url_candidates_from_body(
        {"pdf_url_candidates": ["http://a.com/pdf", "", "   ", 123, "http://b.com/pdf"]}
    )
    assert result == ["http://a.com/pdf", "http://b.com/pdf"]


def test_pdf_url_candidates_from_body_missing_key() -> None:
    assert _pdf_url_candidates_from_body({}) is None


def test_pdf_url_candidates_from_body_not_a_list() -> None:
    assert _pdf_url_candidates_from_body({"pdf_url_candidates": "not-a-list"}) is None


# ---------------------------------------------------------------------------
# line 231 – request_security_error Bearer auth branch
# ---------------------------------------------------------------------------

def test_request_security_error_bearer_auth_valid() -> None:
    security = build_http_security_config(auth_token="my-secret")
    # valid Bearer token
    assert (
        request_security_error(
            method="POST",
            headers={"Authorization": "Bearer my-secret"},
            security=security,
        )
        is None
    )
    # "bearer" (lowercase) is NOT treated as Bearer by the code
    assert request_security_error(
        method="POST",
        headers={"authorization": "bearer my-secret"},
        security=security,
    ) == (401, "invalid API token")


def test_request_security_error_bearer_auth_invalid() -> None:
    security = build_http_security_config(auth_token="my-secret")
    assert request_security_error(
        method="POST",
        headers={"Authorization": "Bearer wrong-token"},
        security=security,
    ) == (401, "invalid API token")


def test_request_security_error_bearer_auth_takes_priority() -> None:
    """Bearer auth takes priority over X-Pzi-Token header when both present."""
    security = build_http_security_config(auth_token="my-secret")
    # Bearer is correct, X-Pzi-Token is wrong → should succeed
    assert (
        request_security_error(
            method="POST",
            headers={
                "Authorization": "Bearer my-secret",
                "X-Pzi-Token": "wrong",
            },
            security=security,
        )
        is None
    )


# ---------------------------------------------------------------------------
# line 245 – validated_content_length negative Content-Length → 400
# ---------------------------------------------------------------------------

def test_validated_content_length_negative() -> None:
    assert validated_content_length("-1", max_body_bytes=100) == (
        400,
        "invalid Content-Length",
    )
    assert validated_content_length("-999", max_body_bytes=100) == (
        400,
        "invalid Content-Length",
    )


# ---------------------------------------------------------------------------
# line 282-283 – _handle_attach_pdf_bytes body not dict → 400
# ---------------------------------------------------------------------------

def test_post_attach_pdf_bytes_non_dict_body_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps(["not", "a", "dict"]).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            resp_body = json.loads(exc.read().decode("utf-8"))
            assert "must be a JSON object" in resp_body.get("error", "")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 287-288 – _handle_attach_pdf_bytes missing citekey → 400
# ---------------------------------------------------------------------------

def test_post_attach_pdf_bytes_missing_citekey_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({"pdf_base64": "AAAA"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            resp_body = json.loads(exc.read().decode("utf-8"))
            assert "citekey" in resp_body.get("error", "")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 290-291 – _handle_attach_pdf_bytes missing pdf_base64 → 400
# ---------------------------------------------------------------------------

def test_post_attach_pdf_bytes_missing_pdf_base64_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({"citekey": "smith2024graph"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            resp_body = json.loads(exc.read().decode("utf-8"))
            assert "pdf_base64" in resp_body.get("error", "")
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------
# line 338-343 – run_server direct call (mock the server class)
# ---------------------------------------------------------------------------

def test_run_server_calls_serve_forever_and_closes() -> None:
    """Verify run_server creates the handler, calls serve_forever, and
    calls server_close in the finally block."""
    server_mock = MagicMock()
    server_class = MagicMock(return_value=server_mock)

    run_server(
        config_path="/tmp/test-config.toml",
        home_dir="/tmp/test-home",
        host="127.0.0.1",
        port=12345,
        server_class=server_class,
    )

    # Check server class was called with correct address and a handler instance
    server_class.assert_called_once()
    args, _kwargs = server_class.call_args
    assert args[0] == ("127.0.0.1", 12345)
    # args[1] is the dynamically created handler class
    handler_cls = args[1]
    assert handler_cls.__name__ == "PziHandler"
    assert handler_cls.server_version == "pzi/0.1"

    server_mock.serve_forever.assert_called_once()
    server_mock.server_close.assert_called_once()


def test_run_server_closes_even_when_serve_forever_raises() -> None:
    """server_close must be called even if serve_forever throws."""
    server_mock = MagicMock()
    server_mock.serve_forever.side_effect = RuntimeError("boom")
    server_class = MagicMock(return_value=server_mock)

    try:
        run_server(
            config_path="/tmp/test-config.toml",
            home_dir="/tmp/test-home",
            host="127.0.0.1",
            port=12346,
            server_class=server_class,
        )
    except RuntimeError:
        pass

    server_mock.serve_forever.assert_called_once()
    server_mock.server_close.assert_called_once()


# ---------------------------------------------------------------------------
# Additional: Bearer auth via HTTP integration (exercises line 231 in live path)
# ---------------------------------------------------------------------------

def test_get_bibs_with_bearer_auth(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path, token="secret")
    try:
        # Without token → 401
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/bibs", timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401

        # With Bearer token → 200
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/bibs",
            headers={"Authorization": "Bearer secret"},
        )
        response = urllib.request.urlopen(req, timeout=2)
        assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()
