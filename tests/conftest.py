"""pytest fixtures for browser integration tests."""

import ipaddress
import os
import socket
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from pzi import url_safety
from pzi.config import (
    DEFAULT_DESKTOP_FALLBACK_HOSTS,
    AppConfig,
    escape_toml_string,
)
from pzi.config import DEFAULT_TRANSLATION_SERVER_URL as _REAL_TRANSLATION_SERVER_URL
from pzi.safe_http import SsrfBlocked
from tests.browser_probe import default_browsers_path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _is_live_test(request) -> bool:
    return "tests/live" in str(getattr(request.node, "path", ""))


# ---------------------------------------------------------------------------
# Test-only config writer
#
# Moved out of `pzi.config` (where it was 93 lines of production code with no
# production caller — its docstring claimed a `pzi init` caller that does not
# exist; init copies the template or uses `setup_service.render_config`).
# It stays partial by design: enough keys for a fixture, not a faithful
# round-trip, which is exactly why it does not belong beside the real loader.
# ---------------------------------------------------------------------------


def _optional_string(key: str, value: str | None) -> list[str]:
    """Return a single TOML key = value line if value is not None."""
    if value is not None:
        return [f'{key} = "{escape_toml_string(value)}"']
    return []


def _optional_int(key: str, value: int | None) -> list[str]:
    """Return a single TOML key = value line if value is not None."""
    if value is not None:
        return [f"{key} = {value}"]
    return []


def _optional_string_list(key: str, value: tuple[str, ...] | None) -> list[str]:
    """Return a TOML key = [...] line if value is not None and non-empty."""
    if value:
        items = ", ".join(f'"{escape_toml_string(item)}"' for item in value)
        return [f"{key} = [{items}]"]
    return []


def _dump_app_config(config: AppConfig) -> str:
    """Serialize an AppConfig to TOML text. **Partial, by design.**

    Emits 23 of ``AppConfig``'s 36 keys. It exists for test fixtures and for
    ``pzi init``-style scaffolding, where a minimal readable file is wanted —
    not as a faithful round-trip of an arbitrary config. Feeding it a config
    that sets any of the keys below and re-reading the result silently drops
    them:

    ``capture_source_dirs``, ``inbox_path``, ``pdf_file_path_style``,
    ``page_metadata_cmd``, ``page_metadata_timeout_seconds``,
    ``metadata_confidence_min_score``, ``promote_confidence_threshold``,
    ``metadata_cache_ttl``, ``browser_hook``, ``pzi_data_home``, ``node_path``,
    ``pdf_discovery_parallel``, ``ezproxy_host``.

    Do not use it to rewrite a user's config file. Adding a key to ``AppConfig``
    does not automatically add it here.
    """
    lines: list[str] = [
        f'translation_server_url = "{escape_toml_string(config["translation_server_url"])}"',
        f'api_listen_host = "{escape_toml_string(config["api_listen_host"])}"',
        f'api_listen_port = {config["api_listen_port"]}',
    ]

    lines.extend(_optional_string("api_auth_token", config.get("api_auth_token")))
    lines.extend(_optional_string("api_auth_token_cmd", config.get("api_auth_token_cmd")))
    lines.extend(_optional_string_list("api_allowed_origins", config.get("api_allowed_origins")))
    lines.extend(_optional_int("api_max_body_bytes", config.get("api_max_body_bytes")))
    lines.extend(_optional_string("contact_email", config.get("contact_email")))
    lines.extend(_optional_string("contact_email_cmd", config.get("contact_email_cmd")))
    lines.extend(_optional_string("unpaywall_email", config.get("unpaywall_email")))
    lines.extend(_optional_string("unpaywall_email_cmd", config.get("unpaywall_email_cmd")))
    lines.extend(
        _optional_string("semantic_scholar_api_key", config.get("semantic_scholar_api_key"))
    )
    lines.extend(
        _optional_string(
            "semantic_scholar_api_key_cmd",
            config.get("semantic_scholar_api_key_cmd"),
        )
    )
    lines.extend(_optional_string("flaresolverr_url", config.get("flaresolverr_url")))
    lines.extend(_optional_string("browser_pdf_cmd", config.get("browser_pdf_cmd")))
    lines.extend(_optional_string("citekey_format", config.get("citekey_format")))
    lines.extend(_optional_string("pdf_filename_format", config.get("pdf_filename_format")))
    lines.extend(_optional_string("api_url", config.get("api_url")))
    lines.extend(_optional_string("browser_profile_path", config.get("browser_profile_path")))
    browser_engine = config.get("browser_engine")
    if browser_engine and browser_engine != "chromium":
        lines.append(f'browser_engine = "{escape_toml_string(browser_engine)}"')


    desktop_hosts = config.get("desktop_fallback_hosts", [])
    # `!=` alone, not `desktop_hosts and ...`: an explicit empty list is a
    # meaningful setting ("no host needs the desktop fallback") and truthiness
    # would drop it, so a round-trip silently restored the defaults.
    if desktop_hosts != DEFAULT_DESKTOP_FALLBACK_HOSTS:
        dq = '"'
        lines.append(
            f"desktop_fallback_hosts = [{', '.join(dq + escape_toml_string(h) + dq for h in desktop_hosts)}]"
        )

    for bib in config["bibs"]:
        lines.append("")
        lines.append("[[bibs]]")
        lines.append(f'name = "{escape_toml_string(bib["name"])}"')
        lines.append(f'path = "{escape_toml_string(bib["path"])}"')
        lines.append(f'papers_dir = "{escape_toml_string(bib["papers_dir"])}"')
        lines.append(f"default = {'true' if bib['default'] else 'false'}")

    return "\n".join(lines) + "\n"


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

    def _resolve(host, port, *, timeout):
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


@pytest.fixture(autouse=True)
def _clear_xdg_env(request, monkeypatch):
    """Unset XDG base-dir vars for the unit suite so default path resolution
    falls back to the injected home_dir / ``$HOME`` and stays hermetic.

    Without this, a developer whose real environment sets ``XDG_CONFIG_HOME``
    / ``XDG_DATA_HOME`` would have those leak into tests that assert
    home-relative defaults (config path, ``pzi_data_home``) — and worse, tests
    could write into the developer's real config/data dirs. Tests that
    specifically exercise XDG behavior re-set these via ``monkeypatch.setenv``.
    """
    if _is_live_test(request):
        return
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


@pytest.fixture
def real_translation_server_url() -> str:
    """The shipped default, captured at import before the autouse patch below.

    Exists so one test can still pin what the default *is*, which
    ``_dead_default_translation_server`` would otherwise make untestable.
    """
    return _REAL_TRANSLATION_SERVER_URL


@pytest.fixture(autouse=True)
def _dead_default_translation_server(request, monkeypatch):
    """Repoint the *default* translation-server URL at a dead port.

    ``dead_port``/``write_app_config`` only protect configs written through
    them. Most config-writing test helpers are module-local and simply omit
    ``translation_server_url``, so the config loader fell back to the real
    default (``127.0.0.1:1969``) and any such test would reach a translation
    server the developer happened to be running — passing or failing based on
    machine state rather than on the code.

    Patching the default itself closes that for every test at once, including
    ones written later that never hear about the fixture. Live tests set the
    URL explicitly, so they are unaffected either way, but they are skipped
    here for the same reason as the other hermeticity fixtures.
    """
    if _is_live_test(request):
        return
    monkeypatch.setattr(
        "pzi.config.DEFAULT_TRANSLATION_SERVER_URL",
        f"http://127.0.0.1:{_free_port()}",
    )


@pytest.fixture(autouse=True)
def _pin_home(request, tmp_path_factory, monkeypatch):
    """Point ``$HOME`` at a throwaway directory for the unit suite.

    Clearing the XDG vars above sends default path resolution to ``$HOME``, so
    a test that forgets to pass ``home_dir`` writes into the developer's *real*
    home — `~/.config/pzi/config.toml`, `~/.local/share/pzi/api_token`. That is
    the same class of leak the XDG fixture exists to stop, one variable over,
    and nothing was catching it.

    ``$HOME`` is not ours alone, though: Playwright resolves its browser cache
    from it, so repointing it hides the downloaded browsers and every browser
    test fails with "Executable doesn't exist at <tmpdir>/.cache/ms-playwright".
    Pin ``PLAYWRIGHT_BROWSERS_PATH`` to the real location first — computed
    before the switch, when ``$HOME`` still points at the real home.
    """
    if _is_live_test(request):
        return
    browsers_path = default_browsers_path()
    if browsers_path is not None:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_path))
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))


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
def dump_app_config():
    """The test-only config writer, as a fixture.

    A fixture rather than a bare import: `tests/live/` has its own
    `conftest.py`, so `from conftest import ...` resolves to whichever pytest
    inserted first, which is not a thing to rely on.
    """
    return _dump_app_config


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
        Path(config_path).write_text(_dump_app_config(config))
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
