"""pytest fixtures for browser integration tests."""

import os
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
