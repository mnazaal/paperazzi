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
                if path == "/set-cookie":
                    # A login: a cookie that outlives the browser.
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", "2")
                    self.send_header("Set-Cookie", "login=1; Path=/; Max-Age=86400")
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return None
                if path == "/login.pdf" and "login=1" not in (self.headers.get("Cookie") or ""):
                    return self._send(403, "text/html", b"login required")
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
def test_a_late_large_page_still_has_its_links_read(server, browser, monkeypatch) -> None:
    """A large page whose response arrives just before the deadline: a 1 s
    floor on the read lost a 15000-link page's PDF link, which e190718 found
    (review-nd10 `probe_disc_big chromium 27 15000 1`). 20000 links take
    Chromium 2.3-2.6 s to read here, Firefox 0.5 s (`link-read-cost.log`)."""
    import pzi.browser_pdf_hook as hook

    # 2.5 s for the 20000-link page to load within the deadline (a 1 s
    # margin timed `goto` out under load); the banner tries then end within
    # a second of it, so the read starts with less time left than it needs.
    monkeypatch.setattr(hook, "DISCOVERY_TIMEOUT_SECONDS", 6.0)
    links = b"".join(b"<a href='/p/%d'>x</a>" % i for i in range(20000))
    server.pages["/big.html"] = links + b"<a href='/real.pdf'>real</a>" + BEACONS
    server.delays["/big.html"] = 3.5
    url, errors, _ = _discover_within(browser, server, "/big.html", 60)
    assert url == server.base + "/real.pdf", errors


@pytest.mark.parametrize("browser", BROWSERS)
def test_the_link_read_is_linear_in_the_page_size(server, browser) -> None:
    """`DISCOVERY_JS` deduplicated with `Array.includes`, quadratic in the
    links: Chromium read 40000 in 11.6 s, past the read's 5 s floor, and
    lost a late page's PDF link (review-st1 `probe_disc_big chromium 20
    40000 1`). 60000 links, read in first-occurrence order, a repeat once.

    The read is timed once the page answers at all: Chromium spends 38 s
    laying out 100000 links before its first `evaluate` of any script
    returns (`first-read-chromium.log`), and no read can shorten that."""
    import pzi.browser_pdf_hook as hook

    n = 60_000
    links = b"".join(b"<a href='/p/%d'></a>\n" % i for i in range(n))
    server.pages["/huge.html"] = links + b"<a href='/p/7'>again</a><a href='/real.pdf'>r</a>"
    with _session(browser, server) as session:
        session.navigate(server.base + "/huge.html", timeout=120_000)
        assert session.evaluate("() => 1") == 1
        found = session.evaluate(hook.DISCOVERY_JS, timeout=hook._LINK_READ_FLOOR_SECONDS)
    expected = [f"{server.base}/p/{i}" for i in range(n)] + [server.base + "/real.pdf"]
    assert found == expected


@pytest.mark.parametrize("browser", BROWSERS)
def test_discovery_past_its_deadline_skips_its_waits(server, browser, short_deadline) -> None:
    """A late page with no PDF link: the click's download wait and the
    network-idle waits ran in full after the deadline (review-nd10
    `NOPDF=1 probe_disc_big 28 10 1`: 39.5 s against a 30 s deadline)."""
    server.pages["/late.html"] = b"<a href='/p/1'>x</a><button>PDF</button>" + BEACONS
    server.delays["/late.html"] = SHORT_T - 0.4
    url, errors, seconds = _discover_within(browser, server, "/late.html", 60)
    assert url is None
    # The banner tries, a read of ten links and leaving an idle page.
    assert seconds < SHORT_T + 1.2 + 3.0
    # Cut short by the deadline, not "no PDF here" (review-st1 `NOPDF=1
    # probe_disc_big chromium 28 10 1`: `errors` was empty).
    assert errors == [f"discovery cut at its deadline: TimeoutError: Timeout at {server.base}"]


def _tree(root: Path) -> dict[str, str]:
    """Every entry under *root*: a link's target, a file's bytes, a mode."""
    import hashlib
    import os

    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = "link:" + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            out[rel] = f"dir:{path.stat().st_mode:o}"
    return out


def test_firefox_runs_on_a_private_copy_of_its_profile(server, tmp_path) -> None:
    """Firefox ran on the user's profile in place and wrote into it
    (owner decision: "Copy it like Chrome"). The copy must still carry the
    profile's login, be private, leave out a link to a directory outside
    the profile, and go on close."""
    import os
    import stat

    from playwright.sync_api import sync_playwright

    profile = tmp_path / "scratch-firefox-profile"
    with sync_playwright() as p:
        context = p.firefox.launch_persistent_context(str(profile), headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(server.base + "/set-cookie")
        context.close()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside")
    (profile / "linkdir").symlink_to(outside)
    os.chmod(profile, 0o755)
    before = _tree(profile)

    with _session("firefox", server, profile_path=str(profile)) as session:
        copy = session._temp_profile
        assert copy is not None and copy != profile
        assert stat.S_IMODE(copy.stat().st_mode) == 0o700
        assert not (bs._clone_profile_dir(copy) / "linkdir").exists()
        result = session.fetch_direct(server.base + "/login.pdf")
        assert result.is_pdf(), "the profile's login cookie was not sent"
    assert not copy.exists()
    assert _tree(profile) == before
    assert (outside / "sentinel.txt").read_text() == "outside"


#: Each browser's cookie database, relative to its profile.
COOKIE_DB = {"chromium": "Default/Cookies", "firefox": "cookies.sqlite"}


@pytest.mark.parametrize("browser", BROWSERS)
def test_a_cookie_database_linked_out_of_the_profile_is_not_written(
    server, tmp_path, browser
) -> None:
    """A link copied as a link let the browser write through it, into the
    file it names outside pzi's copy (review-st1 `probe_symlink_write`).
    The login it holds is still sent."""
    import hashlib
    import os
    import shutil

    from playwright.sync_api import sync_playwright

    profile = tmp_path / "scratch-profile"
    with sync_playwright() as p:
        kind = p.firefox if browser == "firefox" else p.chromium
        context = kind.launch_persistent_context(str(profile), headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(server.base + "/set-cookie")
        page.wait_for_timeout(1000)
        context.close()
    database = profile / COOKIE_DB[browser]
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.move(database, outside / "Cookies.db")
    for side in ("-wal", "-journal", "-shm"):
        Path(f"{database}{side}").unlink(missing_ok=True)
    database.symlink_to(outside / "Cookies.db")

    def outside_state() -> tuple[list[str], str]:
        digest = hashlib.sha256((outside / "Cookies.db").read_bytes()).hexdigest()
        return sorted(os.listdir(outside)), digest

    before = outside_state()
    with _session(browser, server, profile_path=str(profile)) as session:
        assert session.fetch_direct(server.base + "/login.pdf").is_pdf(), (
            "the profile's login cookie was not sent"
        )
        session.navigate(server.base + "/set-cookie")  # a cookie write
        session.page.wait_for_timeout(1500)
    assert outside_state() == before

