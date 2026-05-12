"""Coverage edge tests for http_api.py missing branches."""

import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

from pzi.http_api import (
    build_handler_class,
    build_http_security_config,
    origin_allowed,
    request_security_error,
    validated_content_length,
)


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
    return config_path, bib_path


# -------------------------------------------------------------------
# POST /capture with invalid JSON body (line 145)
# -------------------------------------------------------------------


def test_post_capture_invalid_json_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=b"not json at all {{{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode())
            assert "invalid JSON" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# POST /capture with non-dict body array (line 156-157)
# -------------------------------------------------------------------


def test_post_capture_non_object_body_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps([1, 2, 3]).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode())
            assert "JSON object" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# POST /attach-pdf-bytes with non-dict body (line 282-283)
# -------------------------------------------------------------------


def test_post_attach_pdf_bytes_non_object_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps([1, 2]).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode())
            assert "JSON object" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# POST /attach-pdf-bytes missing citekey (line 287-288)
# -------------------------------------------------------------------


def test_post_attach_pdf_bytes_missing_citekey_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({"pdf_base64": "dGVzdA=="}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode())
            assert "citekey" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# POST /attach-pdf-bytes missing pdf_base64 (line 290-291)
# -------------------------------------------------------------------


def test_post_attach_pdf_bytes_missing_pdf_base64_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({"citekey": "smith2024graph"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/attach-pdf-bytes",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            body = json.loads(exc.read().decode())
            assert "pdf_base64" in body["error"]
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# OPTIONS request with disallowed origin (line 82-83)
# -------------------------------------------------------------------


def test_options_request_with_disallowed_origin_returns_403(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/capture",
            method="OPTIONS",
            headers={"Origin": "https://evil.example"},
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# POST request with invalid Content-Length header (line 134-136)
# -------------------------------------------------------------------


def test_post_capture_invalid_content_length_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        json.dumps({"url": "10.1/test"}).encode("utf-8")
        # Use a raw socket to send an invalid Content-Length header
        import socket as sock_mod
        s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
        s.connect(("127.0.0.1", port))
        s.sendall(
            b"POST /capture HTTP/1.0\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: bad\r\n"
            b"\r\n"
        )
        response = s.recv(4096).decode("utf-8")
        s.close()
        assert "400" in response
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# request_security_error: lowercase authorization header (line 245)
# and lowercase X-Pzi-Token header (line 231)
# -------------------------------------------------------------------


def test_request_security_error_accepts_lowercase_auth_header() -> None:
    security = build_http_security_config(auth_token="secret")
    # Lowercase x-pzi-token
    assert request_security_error(
        method="GET",
        headers={"x-pzi-token": "secret"},
        security=security,
    ) is None
    # Lowercase authorization with Bearer
    assert request_security_error(
        method="GET",
        headers={"authorization": "Bearer secret"},
        security=security,
    ) is None


def test_request_security_error_rejects_wrong_bearer_token() -> None:
    security = build_http_security_config(auth_token="secret")
    assert request_security_error(
        method="GET",
        headers={"Authorization": "Bearer wrong"},
        security=security,
    ) == (401, "invalid API token")


def test_request_security_error_accepts_bearer_token() -> None:
    security = build_http_security_config(auth_token="secret")
    assert request_security_error(
        method="GET",
        headers={"Authorization": "Bearer secret"},
        security=security,
    ) is None


def test_origin_allowed_none_and_empty() -> None:
    security = build_http_security_config()
    assert origin_allowed(None, security["allowed_origins"])
    assert origin_allowed("", security["allowed_origins"])
    assert origin_allowed("  ", security["allowed_origins"])


def test_origin_allowed_exact_match_with_trailing_slash() -> None:
    security = build_http_security_config()
    assert origin_allowed("http://127.0.0.1/", security["allowed_origins"])


# -------------------------------------------------------------------
# GET /bibs with server error (list_bibs returning error - line 122-123)
# This is hard to trigger without a bad config. Test directly.
# -------------------------------------------------------------------


def test_get_bibs_error_returns_500(tmp_path: Path) -> None:
    # Create a config that will cause list_bibs to error
    config_path = tmp_path / "bad_config.toml"
    config_path.write_text("not valid toml {{{")
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/bibs")
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# POST to unknown path (line 130-ish, 404 for POST)
# -------------------------------------------------------------------


def test_post_unknown_path_returns_404(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        body = json.dumps({"x": "y"}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/nope",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=2)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# Content-Length: -1 (negative, line 134-136 for negative length)
# -------------------------------------------------------------------


def test_post_capture_negative_content_length_returns_400(tmp_path: Path) -> None:
    config_path, _ = _seed(tmp_path)
    port, _thread, server = _serve_once(config_path, tmp_path)
    try:
        import socket as sock_mod
        s = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
        s.connect(("127.0.0.1", port))
        s.sendall(
            b"POST /capture HTTP/1.0\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: -1\r\n"
            b"\r\n"
        )
        response = s.recv(4096).decode("utf-8")
        s.close()
        assert "400" in response
    finally:
        server.shutdown()
        server.server_close()


# -------------------------------------------------------------------
# validated_content_length: empty string
# -------------------------------------------------------------------


def test_validated_content_length_empty_string() -> None:
    assert validated_content_length("", max_body_bytes=10) == 0
    assert validated_content_length("  ", max_body_bytes=10) == 0
