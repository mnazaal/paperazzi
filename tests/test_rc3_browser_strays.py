"""Real-browser regressions for the shared session's guard, pages and notes.

Each test drives a real Chromium or Firefox against a loopback server that
records every request it receives. That log is the oracle wherever a test
claims something was or was not requested. No profile, no network.
"""

from __future__ import annotations

import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import pzi.browser_session as bs
from tests.browser_probe import require_browser

pytestmark = pytest.mark.browser

BROWSERS = ["chromium", "firefox"]
PDF = b"%PDF-1.4\n" + b"0" * 4000 + b"\n%%EOF\n"


@pytest.fixture(autouse=True)
def _require_browser() -> None:
    require_browser()


@pytest.fixture(autouse=True)
def _private_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every temp dir pzi or Playwright makes lands here."""
    root = tmp_path / "tmp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


class _Server:
    """Loopback server. `hits` records each request's path and query."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        self.pages: dict[str, bytes] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                outer.hits.append(self.path)
                path = self.path.partition("?")[0]
                if path in outer.pages:
                    return self._send(200, "text/html", outer.pages[path])
                if path.endswith(".pdf"):
                    return self._send(200, "application/pdf", PDF)
                return self._send(404, "text/plain", b"nf")

            def _send(self, status: int, ctype: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    srv = _Server()
    yield srv
    srv.close()


def _session(browser: str, server: _Server, **kwargs):
    """A session that may reach only `server`, and nothing named `private`."""
    return bs.open_browser_session(
        browser,
        url_allowed=lambda u: u.startswith(server.base) and "private" not in u,
        **kwargs,
    )


@pytest.mark.parametrize("browser", BROWSERS)
def test_a_popups_first_request_meets_the_guard(server, browser) -> None:
    """A page's own route never sees a popup's first request, so a popup
    reached a URL `url_allowed` refuses (review-nd5 `probe_nd5 popguard`)."""
    server.pages["/popper.html"] = (
        b"<script>window.open('/private.html?from=popup');</script>"
    )
    with _session(browser, server) as session:
        session.navigate(server.base + "/popper.html")
        session.page.wait_for_timeout(800)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and len(session.page.context.pages) < 2:
            session.page.wait_for_timeout(100)
        opened = len(session.page.context.pages)
    assert "/popper.html" in server.hits
    assert opened >= 2, "the popup never opened, so the test proves nothing"
    assert not any(hit.startswith("/private.html") for hit in server.hits)
