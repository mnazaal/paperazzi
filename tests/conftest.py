"""pytest fixtures for browser integration tests."""

import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from pzi import url_safety

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _hermetic_dns(request, monkeypatch):
    """Make hostname resolution deterministic and offline for the unit suite.

    The SSRF guard (``safe_public_http_url``) resolves real hostnames with a
    0.25s timeout; under network jitter that race intermittently rejects a
    public host, which made capture/discovery tests flaky (e.g. a 200 path
    turning into 400 when DNS was slow). Stub the default resolver so dotted
    public hostnames resolve to a fixed public IP.

    Localhost names and IP literals are gated *before* DNS, so the
    private/loopback rejection tests are unaffected. Tests that exercise the
    resolver logic itself inject their own ``resolve_host``. Live smoke tests
    (``PZI_LIVE=1``, under ``tests/live/``) keep real DNS.
    """
    if "tests/live" in str(getattr(request.node, "path", "")):
        return

    def _resolve(host, port, *, timeout):  # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(url_safety, "resolve_host_with_timeout", _resolve)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FixtureHandler(SimpleHTTPRequestHandler):
    """Serve from FIXTURES_DIR regardless of cwd."""

    def translate_path(self, path):
        rel = path.lstrip("/")
        return str(FIXTURES_DIR / (rel or "index.html"))

    def log_message(self, format, *args):
        pass  # suppress log noise during tests


@pytest.fixture(scope="session")
def http_server():
    """Serve tests/fixtures/ on a free port.  Auto-cleaned after tests."""
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
