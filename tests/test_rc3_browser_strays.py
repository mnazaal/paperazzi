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
        #: Seconds to hold a page's response before answering, by path.
        self.delays: dict[str, float] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                outer.hits.append(self.path)
                path = self.path.partition("?")[0]
                if path in outer.pages:
                    time.sleep(outer.delays.get(path, 0.0))
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


#: Opens a popup that requests /beacon every 100 ms while it is open.
OPENER_PAGE = b"<script>window.open('/beacon-pop.html');</script>"
BEACON_PAGE = (
    b"<p>pop</p><script>setInterval(function () {"
    b" fetch('/beacon?' + Math.random()); }, 100);</script>"
)
#: Opens a popup that sets its opener's location to /beat.html every 5 ms
#: unless the opener already shows it (review-nd9 `probe_reopen9`).
REOPEN_PAGE = b"<script>window.open('/reopener.html');</script>"
REOPENER_PAGE = (
    b"<p>rpop</p><script>setInterval(function () { var here = '';"
    b" try { here = opener.location.pathname; } catch (e) {}"
    b" if (here !== '/beat.html') { try { opener.location = '/beat.html'; } catch (e) {} }"
    b" }, 5);</script>"
)


def _serve_beacons(server: _Server) -> None:
    server.pages.update(
        {
            "/opener.html": OPENER_PAGE,
            "/beacon-pop.html": BEACON_PAGE,
            "/reopen.html": REOPEN_PAGE,
            "/reopener.html": REOPENER_PAGE,
            "/beat.html": BEACON_PAGE,
        }
    )


def _beacons(server: _Server) -> int:
    return sum(1 for hit in server.hits if hit.startswith("/beacon"))


def _assert_idle_after_return(server: _Server, session) -> None:
    """After the call returns, no page of the session requests anything
    while pzi makes no Playwright call, and only the session's own page is
    left, on `about:blank`."""
    before = _beacons(server)
    time.sleep(1.5)  # no Playwright call: nothing pzi does can intervene
    assert _beacons(server) == before
    assert [page.url for page in session.page.context.pages] == ["about:blank"]


@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("landing", ["/opener.html", "/reopen.html"])
def test_discovery_leaves_no_live_page(server, browser, landing) -> None:
    from pzi.browser_pdf_hook import discover_pdf_url

    _serve_beacons(server)
    with _session(browser, server) as session:
        assert discover_pdf_url(server.base + landing, _session=session) is None
        assert _beacons(server) > 0, "the popup never ran, so the test proves nothing"
        _assert_idle_after_return(server, session)


@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("challenge_timeout", [0, 2])
def test_a_download_attempt_leaves_no_live_page(server, browser, challenge_timeout) -> None:
    """Step 2, the challenge wait and step 3 all use the session's page."""
    from pzi.browser_pdf_hook import download_pdf

    _serve_beacons(server)
    with _session(browser, server) as session:
        body = download_pdf(
            server.base + "/opener.html",
            _session=session,
            challenge_timeout=challenge_timeout,
            candidate_deadline_seconds=1,
        )
        assert body is None
        assert _beacons(server) > 0, "the popup never ran, so the test proves nothing"
        _assert_idle_after_return(server, session)


#: A page whose script spins for ever 300 ms after it loads (review-nd6
#: `probe_close busy`): `Page.evaluate` on it never answers.
BUSY_PAGE = b"<a href='/none.pdf'>x</a><script>setTimeout(() => { for (;;) {} }, 300);</script>"
#: Keeps the network busy, so waiting for network idle never ends.
BEACONS = b"<script>setInterval(function () { fetch('/beacon?' + Math.random()); }, 100);</script>"


def _discover_within(
    browser: str, server: _Server, path: str, seconds: float
) -> tuple[object, list[str], float]:
    """Launch a session and run discovery on a thread of their own, giving up
    after *seconds*, so an unbounded read fails the test instead of stalling
    the run. Playwright's objects belong to the thread that made them."""
    from pzi.browser_pdf_hook import discover_pdf_url

    out: dict[str, object] = {}
    errors: list[str] = []

    def run() -> None:
        try:
            with _session(browser, server) as session:
                started = time.monotonic()
                out["url"] = discover_pdf_url(server.base + path, _session=session, errors=errors)
                out["seconds"] = time.monotonic() - started
        except BaseException as exc:  # reported below, on the test's thread
            out["raised"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"discovery still running after {seconds} s"
    if "raised" in out:
        raise AssertionError("the discovery thread raised") from out["raised"]  # type: ignore[arg-type]
    return out["url"], errors, float(out["seconds"])  # type: ignore[arg-type]


#: Discovery's deadline in these tests, so a hang shows in seconds.
SHORT_T = 3.0
#: What discovery may take past its deadline: the banner tries (1.2 s), the
#: link read's floor, and leaving the page (a spinning page is replaced).
#: The tests allow this much on top.
OVERRUN = 1.2 + 5.0 + 9.0


@pytest.fixture
def short_deadline(monkeypatch):
    import pzi.browser_pdf_hook as hook

    monkeypatch.setattr(hook, "DISCOVERY_TIMEOUT_SECONDS", SHORT_T, raising=False)


@pytest.mark.parametrize("browser", BROWSERS)
def test_discovery_on_a_spinning_page_ends_by_its_deadline(
    server, browser, short_deadline
) -> None:
    """`Page.evaluate` has no timeout, so discovery hung with the session's
    lock held (review-nd6 `probe_close busy`)."""
    server.pages["/busy.html"] = BUSY_PAGE
    url, errors, seconds = _discover_within(browser, server, "/busy.html", SHORT_T + OVERRUN + 10)
    assert url is None
    assert seconds < SHORT_T + OVERRUN
    assert any("links not read" in note for note in errors), errors


@pytest.mark.parametrize("browser", BROWSERS)
def test_a_late_large_page_still_has_its_links_read(server, browser, short_deadline) -> None:
    """A large page whose response arrives just before the deadline: a 1 s
    floor on the read lost a 15000-link page's PDF link, which e190718 found
    (review-nd10 `probe_disc_big chromium 27 15000 1`). 20000 links take
    Chromium 2.3-2.6 s to read here, Firefox 0.5 s (`link-read-cost.log`)."""
    links = b"".join(b"<a href='/p/%d'>x</a>" % i for i in range(20000))
    server.pages["/big.html"] = links + b"<a href='/real.pdf'>real</a>" + BEACONS
    # Loaded within the deadline; the banner tries then end past it.
    server.delays["/big.html"] = SHORT_T - 1.0
    url, errors, _ = _discover_within(browser, server, "/big.html", 60)
    assert url == server.base + "/real.pdf", errors


@pytest.mark.parametrize("browser", BROWSERS)
def test_discovery_past_its_deadline_skips_its_waits(server, browser, short_deadline) -> None:
    """A late page with no PDF link: the click's download wait and the
    network-idle waits ran in full after the deadline (review-nd10
    `NOPDF=1 probe_disc_big 28 10 1`: 39.5 s against a 30 s deadline)."""
    server.pages["/late.html"] = b"<a href='/p/1'>x</a><button>PDF</button>" + BEACONS
    server.delays["/late.html"] = SHORT_T - 0.4
    url, _, seconds = _discover_within(browser, server, "/late.html", 60)
    assert url is None
    # The banner tries, a read of ten links and leaving an idle page.
    assert seconds < SHORT_T + 1.2 + 3.0
