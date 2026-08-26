"""Tests for the CLI-side client that delegates browser work to the server."""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from pzi import server_browser


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _capture_urlopen(monkeypatch, payload: dict[str, Any]) -> list[Any]:
    captured: list[Any] = []

    def fake_urlopen(req: Any, timeout: float = 0) -> _FakeResponse:
        captured.append(req)
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(server_browser, "urlopen", fake_urlopen)
    return captured


def test_discover_sends_auth_token_under_x_pzi_token_header(monkeypatch) -> None:
    """Regression: the token must use X-Pzi-Token (what the server reads), not X-Pzi-Auth."""
    captured = _capture_urlopen(monkeypatch, {"pdf_url": "https://example.com/p.pdf"})

    result = server_browser.discover_via_server_api(
        "http://127.0.0.1:8765", "https://example.com/article", auth_token="secret",
    )

    assert result == "https://example.com/p.pdf"
    headers = captured[0].headers
    assert headers.get("X-pzi-token") == "secret"  # urllib title-cases header keys
    assert "X-pzi-auth" not in headers


def test_discover_reports_unreachable_server_distinctly(dead_port: int) -> None:
    """The twin ``download_via_server_api`` tells "no PDF" apart from "nothing
    was listening" via its ``errors`` list; ``discover_via_server_api`` must
    do the same instead of collapsing both into a bare ``None``.
    """
    errors: list[str] = []
    result = server_browser.discover_via_server_api(
        f"http://127.0.0.1:{dead_port}", "https://example.com/article",
        timeout=2, errors=errors,
    )
    assert result is None
    assert errors and "not reachable" in errors[0]


def test_discover_reports_no_pdf_distinctly_from_unreachable(monkeypatch) -> None:
    """The contrast: a server that answers but has no PDF is a different
    diagnostic than one that never answered at all.
    """
    _capture_urlopen(monkeypatch, {"pdf_url": None})

    errors: list[str] = []
    result = server_browser.discover_via_server_api(
        "http://127.0.0.1:8765", "https://example.com/article", errors=errors,
    )
    assert result is None
    assert errors and "no PDF" in errors[0]
    assert "not reachable" not in errors[0]


def test_download_sends_auth_token_under_x_pzi_token_header(monkeypatch) -> None:
    pdf_b64 = base64.b64encode(b"%PDF-1.4\n").decode()
    captured = _capture_urlopen(monkeypatch, {"pdf_base64": pdf_b64})

    result = server_browser.download_via_server_api(
        "http://127.0.0.1:8765", "https://example.com/p.pdf", auth_token="secret",
    )

    assert result == b"%PDF-1.4\n"
    headers = captured[0].headers
    assert headers.get("X-pzi-token") == "secret"
    assert "X-pzi-auth" not in headers
