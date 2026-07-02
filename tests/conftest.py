"""pytest fixtures for browser integration tests."""

import ipaddress
import os
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from pzi import url_safety
from pzi.config import dump_app_config
from pzi.safe_http import SsrfBlocked

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _is_live_test(request) -> bool:
    return "tests/live" in str(getattr(request.node, "path", ""))


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
    if _is_live_test(request):
        return

    def _resolve(host, port, *, timeout):  # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(url_safety, "resolve_host_with_timeout", _resolve)


class _NonLoopbackConnectError(SsrfBlocked):
    """Raised when a test attempts a real connect() to a non-loopback address.

    Subclasses ``SsrfBlocked`` (rather than a plain ``OSError``) so the
    existing retry-avoidance logic in ``fetch_helpers._is_ssrf_block`` treats
    a blocked connect as terminal. Otherwise ``fetch_text``'s generic
    ``OSError`` retry branch mistakes it for a transient network error and
    retries with exponential backoff (observed: ~9s added per unmocked
    fetcher call across a test file, from 2 retried sleeps x 3 unmocked
    metadata-provider fetchers)."""


def _sockaddr_ip(address) -> str | None:
    """Extract the destination IP string from a socket address, if present.

    ``address`` is whatever was passed to ``socket.connect``/``connect_ex``:
    an ``(ip_or_host, port)`` tuple for AF_INET, a longer tuple for AF_INET6,
    or (rarely) something else (e.g. AF_UNIX paths) that we don't care about.
    """
    if isinstance(address, tuple) and len(address) >= 2:
        return str(address[0])
    return None


@pytest.fixture(autouse=True)
def _block_non_loopback_sockets(request, monkeypatch):
    """Prevent any test from making a real outbound (non-loopback) connection.

    ``_hermetic_dns`` above makes hostname resolution deterministic by
    resolving public hostnames to a fixed public IP (93.184.216.34) instead
    of hitting real DNS. That alone doesn't stop a test that forgot to mock
    an outbound call (metadata API, PDF download, etc.) from actually
    connecting to that IP over the network. This fixture is the safety net:
    it wraps ``socket.socket.connect``/``connect_ex`` and raises immediately
    for any destination that isn't loopback (127.0.0.0/8 or ::1), so a
    missing mock fails fast and deterministically instead of hanging or
    reaching out to the internet.

    Loopback connections (the ``http_server`` fixture, a config-seeded
    ``translation_server_url`` on 127.0.0.1, etc.) are left untouched — they
    either succeed against a real local listener or fail with a normal
    connection-refused, both of which are fine.

    Live smoke tests (``PZI_LIVE=1``, under ``tests/live/``) keep real
    sockets, matching ``_hermetic_dns``.
    """
    if _is_live_test(request):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(address) -> None:
        ip_str = _sockaddr_ip(address)
        if ip_str is None:
            return
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # Not a plain IP literal (e.g. an unresolved hostname somehow
            # reached connect()). Real DNS is already blocked by
            # ``_hermetic_dns``, so this shouldn't happen; fail closed.
            raise _NonLoopbackConnectError(
                f"blocked connect() to unresolved host {ip_str!r} in test suite "
                "(non-loopback sockets are disabled; see _block_non_loopback_sockets)"
            ) from None
        if not ip.is_loopback:
            raise _NonLoopbackConnectError(
                f"blocked connect() to non-loopback address {ip_str!r} in test suite "
                "(see _block_non_loopback_sockets in tests/conftest.py)"
            )

    def _guarded_connect(self, address):
        _check(address)
        return real_connect(self, address)

    def _guarded_connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def dead_port() -> int:
    """A per-test ephemeral port guaranteed to have nothing listening on it.

    Used in place of hardcoded port literals (e.g. the real translation-server
    default 1969) when seeding test configs, so a real server the developer
    happens to have running locally can never be hit accidentally.
    """
    return _free_port()


@pytest.fixture
def write_app_config(dead_port):
    """Write a minimal ``config.toml`` under a temp home dir; return its path.

    Callable fixture: ``write_app_config(home, bib_name="ml", **extra)``.
    ``home`` is a directory path (``str`` or ``Path``); ``extra`` keys are
    merged into the config dict verbatim (e.g. ``contact_email=...``).

    ``translation_server_url`` defaults to a per-test dead port (see
    ``dead_port``) rather than the real default 1969, so a test that forgets
    to mock a translation-server call fails deterministically instead of
    risking a hit against a real local server a developer happens to have
    running.

    Consolidates what were previously ~identical ``_write_config`` helpers
    duplicated across several test modules.
    """

    def _write(home, bib_name: str = "ml", **extra) -> str:
        home = str(home)
        config_path = os.path.join(home, ".config", "pzi", "config.toml")
        bib_path = os.path.join(home, f"{bib_name}.bib")
        papers_dir = os.path.join(home, "papers")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        os.makedirs(papers_dir, exist_ok=True)
        config = {
            "bibs": [
                {"name": bib_name, "path": bib_path, "papers_dir": papers_dir, "default": True}
            ],
            "translation_server_url": f"http://127.0.0.1:{dead_port}",
            "api_listen_host": "127.0.0.1",
            "api_listen_port": 8765,
            **extra,
        }
        Path(config_path).write_text(dump_app_config(config))
        return config_path

    return _write


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
