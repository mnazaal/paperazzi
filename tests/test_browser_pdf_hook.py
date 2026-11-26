import base64
import io
import json
import subprocess
import sys
import types
from typing import Any, cast

import pytest

from pzi import browser_pdf_hook as hook
from pzi.browser_session import FetchResult
from tests.fake_session import FakeBrowserSession, make_fake_response, make_pdf_response


def test_parse_hook_request_rejects_non_dict() -> None:
    assert hook.parse_hook_request(["nope"]) is None


def test_parse_hook_request_normalizes_discover_url() -> None:
    assert hook.parse_hook_request({"page_url": " https://example.test/article "}) == (
        "discover",
        "https://example.test/article",
    )


def test_parse_hook_request_normalizes_download_url() -> None:
    assert hook.parse_hook_request(
        {"action": "download_pdf", "pdf_url": " https://example.test/paper.pdf "}
    ) == ("download_pdf", "https://example.test/paper.pdf")


def test_parse_hook_request_rejects_missing_urls() -> None:
    assert hook.parse_hook_request({"page_url": "   "}) is None
    assert hook.parse_hook_request({"action": "download_pdf", "pdf_url": None}) is None


def test_encode_hook_response_empty_pdf_url_and_bytes() -> None:
    assert hook.encode_hook_response() == "{}"
    assert hook.encode_hook_response(pdf_url="") == "{}"
    assert hook.encode_hook_response(pdf_bytes=b"") == "{}"


def test_encode_hook_response_pdf_url() -> None:
    assert json.loads(hook.encode_hook_response(pdf_url="https://example.test/paper.pdf")) == {
        "pdf_url": "https://example.test/paper.pdf"
    }


def test_encode_hook_response_pdf_bytes() -> None:
    encoded = json.loads(hook.encode_hook_response(pdf_bytes=b"%PDF-test"))["pdf_base64"]
    assert base64.b64decode(encoded) == b"%PDF-test"


def test_browser_launch_options_for_firefox_disable_pdf_viewer() -> None:
    from pzi.browser_session import browser_launch_options
    assert browser_launch_options("chromium") == {"headless": True}
    assert browser_launch_options("firefox") == {
        "headless": True,
        "firefox_user_prefs": {
            "browser.download.folderList": 2,
            "browser.download.manager.showWhenStarting": False,
            "pdfjs.disabled": True,
        },
    }


def test_resolve_pdf_candidate_urls_filters_normalizes_and_deduplicates() -> None:
    assert hook.resolve_pdf_candidate_urls(
        "https://journal.test/articles/1",
        [
            " /files/paper.pdf ",
            "/files/paper.pdf",
            "https://journal.test/download?id=1",
            "mailto:editor@example.test",
            None,
            "https://journal.test/supplement.html",
        ],
    ) == [
        "https://journal.test/files/paper.pdf",
        "https://journal.test/download?id=1",
    ]


def test_main_discovers_pdf_url(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["pzi-browser-hook", "--browser", "firefox", "--profile", "prof"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"page_url":"https://example.test/a"}'))
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: True)
    monkeypatch.setattr(
        hook,
        "discover_pdf_url",
        lambda page_url, *, browser, profile_path, headless=True, errors=None: (
            f"{page_url}/paper.pdf" if browser == "firefox" and profile_path == "prof" else None
        ),
    )

    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "pdf_url": "https://example.test/a/paper.pdf"
    }


def test_main_downloads_pdf(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi-browser-hook"])
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO('{"action":"download_pdf","pdf_url":"https://example.test/p.pdf"}'),
    )
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: True)
    monkeypatch.setattr(
        hook,
        "download_pdf",
        lambda pdf_url, *, browser, profile_path, headless=True, challenge_timeout=0: b"%PDF-test",
    )

    assert hook.main() == 0
    encoded = json.loads(capsys.readouterr().out)["pdf_base64"]
    assert base64.b64decode(encoded) == b"%PDF-test"


def test_main_returns_empty_for_bad_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi-browser-hook"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: True)

    assert hook.main() == 0
    assert capsys.readouterr().out.strip() == "{}"


def test_main_returns_error_when_browser_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi-browser-hook"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: False)

    assert hook.main() == 1
    assert capsys.readouterr().out.strip() == "{}"
"""Tests for browser_pdf_hook using FakeBrowserSession (no Playwright needed)."""
# === Pure helper: _is_pdf_url ===

def test_is_pdf_url() -> None:
    assert hook._is_pdf_url("https://example.com/paper.pdf")
    assert hook._is_pdf_url("https://example.com/paper.PDF")
    assert not hook._is_pdf_url("https://example.com/paper.html")


# === JS builder constants ===

def test_discovery_js_string() -> None:
    assert "querySelectorAll" in hook.DISCOVERY_JS


# === discover_pdf_url with FakeBrowserSession ===

#: A navigation response: `discover_pdf_url` reads the page only after a real
#: load (`goto` answers None for a same-document navigation, which loads nothing).
_LOADED_PAGE = object()


def test_discover_no_candidates() -> None:
    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], evaluate_results=[[]])
    result = hook.discover_pdf_url("https://example.com", _session=s)
    assert result is None
    assert ("evaluate", hook.DISCOVERY_JS) in s.calls


def test_discover_no_candidates_leaves_errors_empty() -> None:
    """A page that genuinely has no PDF must not look like a broken session."""
    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], evaluate_results=[[]])
    errs: list[str] = []
    result = hook.discover_pdf_url("https://example.com", _session=s, errors=errs)
    assert result is None
    assert errs == []
    assert ("evaluate", hook.DISCOVERY_JS) in s.calls


def test_discover_reports_crashed_session_in_errors() -> None:
    """A `RuntimeError` from a closed/crashed session (G1) must be surfaced,
    not silently read as "no PDF found" — the same class of bug tier 1 fixed
    for a dead capture server (A6)."""
    s = FakeBrowserSession()
    s.close()  # _check_open() now raises RuntimeError on every method call
    errs: list[str] = []
    result = hook.discover_pdf_url("https://example.com", _session=s, errors=errs)
    assert result is None
    assert errs and "browser session is closed" in errs[0]


def test_discover_without_errors_param_still_swallows_crash() -> None:
    """Callers that don't ask for diagnostics keep the old silent-`None` shape."""
    s = FakeBrowserSession()
    s.close()
    assert hook.discover_pdf_url("https://example.com", _session=s) is None


def test_discover_candidates_found() -> None:
    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], evaluate_results=[["/paper.pdf"]])
    result = hook.discover_pdf_url(
        "https://example.com",
        _session=s,
        _resolve=lambda page_url, c: ["https://example.com/paper.pdf"],
    )
    assert result == "https://example.com/paper.pdf"


def test_discover_post_click_pdf() -> None:
    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], 
        url="https://journal.test/download.pdf",
        evaluate_results=[[]],
    )
    result = hook.discover_pdf_url(
        "https://example.com",
        _session=s,
        _click=lambda page: True,
        _resolve=lambda url, c: [],
    )
    assert result == "https://journal.test/download.pdf"


# === download_pdf with FakeBrowserSession ===

def test_download_direct_pdf() -> None:
    s = FakeBrowserSession(
        fetch_result=(200, "application/pdf", b"%PDF-1.4 test"),
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 test"


def test_download_direct_non_pdf() -> None:
    s = FakeBrowserSession(
        fetch_result=(200, "text/html", b"<html></html>"),
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result is None


def test_download_goto_pdf() -> None:
    s = FakeBrowserSession(
        fetch_result=(-1, None, b""),
        goto_results=[make_pdf_response()],
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 test"


def test_download_candidate_link_found() -> None:
    _html = make_pdf_response(body=b"%PDF-1.4 linked")  # not used directly
    s = FakeBrowserSession(
        # The landing URL is not a PDF; the candidate link is. Both go through
        # `fetch_direct` — navigating at a PDF makes Chromium download it, so
        # the candidate sweep never saw a response at all.
        fetch_results=[
            (200, "text/html", b"<html>"),
            (200, "application/pdf", b"%PDF-1.4 linked"),
        ],
        goto_results=[
            type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"}),
        ],
        evaluate_results=[["https://journal.test/linked.pdf"]],
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 linked"


def test_download_candidate_non_string_skipped() -> None:
    html = type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"})
    s = FakeBrowserSession(
        fetch_results=[
            (200, "text/html", b"<html>"),
            (200, "application/pdf", b"%PDF-1.4 found"),
        ],
        goto_results=[html],
        evaluate_results=[[None, 123, "https://journal.test/paper.pdf"]],
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 found"


def test_download_outer_exception() -> None:
    s = FakeBrowserSession()

    def failing_navigate(url, **kw):
        raise RuntimeError("bang")

    s.navigate = failing_navigate
    s.fetch_direct = lambda url: FetchResult(status=-1, content_type=None, body=b"")
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result is None


def test_download_reports_crashed_session_in_errors() -> None:
    """Sibling of test_discover_reports_crashed_session_in_errors (G1): a
    session that dies mid-download is surfaced via `errors`, not folded into
    the ordinary "no PDF" outcome."""
    s = FakeBrowserSession(fetch_result=(-1, None, b""))
    s.close()
    errs: list[str] = []
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s, errors=errs)
    assert result is None
    assert errs and "browser session is closed" in errs[0]

# === FakeBrowserSession raises after close(), like the real session (D1) ===
#
# Before this fix `_check_open()` existed on the fake with the docstring
# "Raise like the real session does once the browser is gone" but no method
# called it — so hook code that reused a session after closing it (a plausible
# bug shape: a retry path, or a helper that closes on one branch and is called
# again on another) passed every fake-based test here and would only fail
# live, against the real `BrowserSession` (src/pzi/browser_session.py), whose
# `navigate`/`evaluate`/`fetch_direct`/`current_url` all call `_check_open()`
# and raise `RuntimeError` once closed.


def test_fake_session_navigate_raises_once_closed() -> None:
    s = FakeBrowserSession()
    s.close()
    with pytest.raises(RuntimeError):
        s.navigate("https://example.com")


def test_fake_session_evaluate_raises_once_closed() -> None:
    s = FakeBrowserSession()
    s.close()
    with pytest.raises(RuntimeError):
        s.evaluate("document.title")


def test_fake_session_fetch_direct_raises_once_closed() -> None:
    s = FakeBrowserSession()
    s.close()
    with pytest.raises(RuntimeError):
        s.fetch_direct("https://example.com/paper.pdf")


def test_fake_session_current_url_raises_once_closed() -> None:
    s = FakeBrowserSession()
    s.close()
    with pytest.raises(RuntimeError):
        s.current_url()


class FakeLocatorFirst:
    def __init__(self, should_click: bool) -> None:
        self.should_click = should_click

    def click(self, *, timeout: int) -> None:
        if not self.should_click:
            raise RuntimeError("not found")


class FakeLocator:
    def __init__(self, page, selector: str) -> None:
        self.first = FakeLocatorFirst(selector in page.clickable_selectors)


class FakeClickPage:
    def __init__(self, clickable_selectors: set[str]) -> None:
        self.clickable_selectors = clickable_selectors

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


def test_cookie_banner_and_download_click_helpers_try_selectors() -> None:
    accept_page = FakeClickPage({"button:has-text('I agree')"})
    hook._dismiss_cookie_banners(accept_page)

    download_page = FakeClickPage({"button:has-text('Download PDF')"})
    assert hook._click_downloadish_links(download_page) is True
    assert hook._click_downloadish_links(FakeClickPage(set())) is False


def test_ensure_browser_installs_missing_browser_binaries(monkeypatch) -> None:
    events: list[str] = []

    class BrowserType:
        def launch(self, *, headless: bool):
            raise RuntimeError("browser missing")

    class Playwright:
        firefox = BrowserType()
        chromium = BrowserType()

        def stop(self) -> None:
            events.append("stop")

    class Starter:
        def start(self) -> Playwright:
            return Playwright()

    fake_sync_api = types.ModuleType("playwright.sync_api")
    setattr(fake_sync_api, "sync_playwright", lambda: Starter())
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check, capture_output: events.append("install:" + cmd[-1]),
    )

    assert hook._ensure_browser("chromium") is True
    assert events == ["stop", "install:chromium"]


def test_ensure_browser_reports_install_failure(monkeypatch) -> None:
    class BrowserType:
        def launch(self, *, headless: bool):
            raise RuntimeError("browser missing")

    class Playwright:
        firefox = BrowserType()
        chromium = BrowserType()

    class Starter:
        def start(self) -> Playwright:
            return Playwright()

    fake_sync_api = types.ModuleType("playwright.sync_api")
    setattr(fake_sync_api, "sync_playwright", lambda: Starter())
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    def fail_install(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", fail_install)

    assert hook._ensure_browser("firefox") is False


def test_discover_pdf_url_returns_post_click_pdf_url() -> None:
    from tests.fake_session import FakeBrowserSession
    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], 
        url="https://journal.test/download.pdf",
        evaluate_results=[[]],
    )
    result = hook.discover_pdf_url(
        "https://journal.test/article",
        _session=s,
        _click=lambda page: True,
        _resolve=lambda url, c: [],
    )
    assert result == "https://journal.test/download.pdf"


def test_discover_pdf_url_returns_post_click_candidate() -> None:
    from tests.fake_session import FakeBrowserSession
    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], 
        evaluate_results=[[], ["/reader/download?id=1"]],
    )
    result = hook.discover_pdf_url(
        "https://journal.test/article",
        _session=s,
        _click=lambda page: True,
        _resolve=lambda page_url, candidates: [
            "https://journal.test/reader/download?id=1"
        ],
    )
    assert result == "https://journal.test/reader/download?id=1"


def test_download_pdf_uses_direct_request_when_pdf() -> None:
    from tests.fake_session import FakeBrowserSession
    s = FakeBrowserSession(
        fetch_result=(200, "application/pdf", b"%PDF-direct"),
    )
    result = hook.download_pdf("https://journal.test/paper.pdf", _session=s)
    assert result == b"%PDF-direct"


def test_download_pdf_does_not_sweep_a_page_its_navigation_never_reached() -> None:
    """A failed navigation leaves the shared page on the previous request's page.

    Sweeping it returned paper A's PDF for a request about paper B (review of
    414506c, executed against real Chromium).
    """
    from tests.fake_session import FakeBrowserSession

    class StalePage(FakeBrowserSession):
        def navigate(self, url, **kwargs):
            self.calls.append(("navigate", url))
            raise RuntimeError("Download is starting")

    s = StalePage(
        fetch_results=[
            (200, "text/html", b"<html>"),
            (200, "application/pdf", b"%PDF-paper-A"),
        ],
        evaluate_results=[["https://journal.test/paperA.pdf"]],
    )
    result = hook.download_pdf("https://journal.test/paperB.pdf", _session=s)
    assert result is None
    assert ("fetch_direct", "https://journal.test/paperA.pdf") not in s.calls


def _raising_session(*, start_url, lands_on, error):
    from tests.fake_session import FakeBrowserSession

    class Raising(FakeBrowserSession):
        def navigate(self, url, **kwargs):
            self.calls.append(("navigate", url))
            if lands_on is not None:
                self._url = lands_on
            raise error

    return Raising(
        url=start_url,
        fetch_results=[
            (200, "text/html", b"<html>"),
            (200, "application/pdf", b"%PDF-swept"),
        ],
        evaluate_results=[["https://journal.test/linked.pdf"]],
    )


def test_download_pdf_ignores_a_stale_page_at_the_same_url() -> None:
    """An earlier request's load of this very URL is not this request's page."""
    url = "https://journal.test/toggle"
    s = _raising_session(start_url=url, lands_on=None, error=RuntimeError("Download is starting"))
    assert hook.download_pdf(url, _session=s) is None


def test_download_pdf_never_sweeps_a_page_the_ssrf_guard_refused() -> None:
    from pzi.safe_http import SsrfBlocked

    url = "https://journal.test/rebind"
    s = _raising_session(
        start_url="https://journal.test/previous",
        lands_on=url,
        error=SsrfBlocked("browser landed on a non-public URL"),
    )
    assert hook.download_pdf(url, _session=s) is None


@pytest.mark.parametrize(
    "lands_on",
    ["https://journal.test/landing", "https://journal.test/previous?t=3"],
    ids=["moved-to-a-new-url", "stale-document-rewrote-its-own-url"],
)
def test_download_pdf_never_sweeps_after_a_failed_navigation(lands_on: str) -> None:
    """A changed URL does not prove this request loaded the page.

    A stale single-page app rewrites its own URL (`pushState`) while the old
    document stays loaded; sweeping it returned paper A's PDF for paper B
    (review of a322bf9, real Chromium). A slow page of this request's own is
    lost too, by design.
    """
    s = _raising_session(
        start_url="https://journal.test/previous",
        lands_on=lands_on,
        error=TimeoutError("Timeout 60000ms exceeded"),
    )
    assert hook.download_pdf("https://resolver.test/doi", _session=s) is None
    assert not any(call[0] == "evaluate" for call in s.calls)


def test_discover_pdf_url_never_reads_a_page_a_same_document_navigation_left() -> None:
    from tests.fake_session import FakeBrowserSession

    s = FakeBrowserSession(
        url="https://journal.test/b-landing",
        evaluate_results=[["https://journal.test/paperA.pdf"]],
    )
    assert hook.discover_pdf_url("https://journal.test/b-landing#main", _session=s) is None
    assert not any(call[0] == "evaluate" for call in s.calls)


def test_download_pdf_never_sweeps_after_a_same_document_navigation() -> None:
    """`goto` returns None when only the fragment changes: nothing was loaded."""
    from tests.fake_session import FakeBrowserSession

    s = FakeBrowserSession(
        url="https://journal.test/b-landing",
        fetch_results=[(200, "text/html", b"<html>"), (200, "application/pdf", b"%PDF-A")],
        evaluate_results=[["https://journal.test/paperA.pdf"]],
    )
    assert hook.download_pdf("https://journal.test/b-landing#main", _session=s) is None
    assert not any(call[0] == "evaluate" for call in s.calls)


def test_challenge_wait_refusal_stops_the_sweep() -> None:
    """A challenge-loop reload refused by the landing check must not be swept."""
    from pzi.safe_http import SsrfBlocked
    from tests.fake_session import FakeBrowserSession

    class RefusedOnReload(FakeBrowserSession):
        navigations = 0

        def navigate(self, url, **kwargs):
            self.calls.append(("navigate", url))
            self.navigations += 1
            if self.navigations == 1:
                return type("R", (), {"headers": {"content-type": "text/html"}, "status": 200})()
            raise SsrfBlocked("browser landed on a non-public URL")

        def wait_for_timeout(self, ms):
            pass

    s = RefusedOnReload(
        fetch_results=[(200, "text/html", b"<html>"), (200, "text/html", b"<html>")],
        evaluate_results=[["https://journal.test/refused.pdf"]],
    )
    assert hook.download_pdf("https://journal.test/chal", _session=s, challenge_timeout=5) is None
    assert not any(call[0] == "evaluate" for call in s.calls)


def test_download_pdf_follows_candidate_links_after_html_page() -> None:
    from tests.fake_session import FakeBrowserSession
    s = FakeBrowserSession(
        # Landing page first, then the candidate link — both through
        # `fetch_direct`, since navigating at a PDF makes Chromium download it
        # and the sweep then saw no response at all.
        fetch_results=[
            (200, "text/html", b"<html>"),
            (200, "application/pdf", b"%PDF-linked"),
        ],
        goto_results=[
            type("F", (), {
                "headers": {"content-type": "text/html"},
                "body": lambda: b"<html>",
            }),
        ],
        evaluate_results=[["https://journal.test/linked.pdf"]],
    )
    result = hook.download_pdf("https://journal.test/article", _session=s)
    assert result == b"%PDF-linked"


# ---------------------------------------------------------------------------
# The candidate loop is bounded
# ---------------------------------------------------------------------------


class _CandidateSession:
    """A session whose page returns as many PDF candidates as it likes."""

    def __init__(self, candidates: list[str], *, navigate_seconds: float = 0.0) -> None:
        self._candidates = candidates
        self._navigate_seconds = navigate_seconds
        #: Every URL the sweep reached for, by whichever mechanism. The cost is
        #: recorded here rather than in `navigate` because the candidate loop
        #: fetches directly now — Chromium turns a navigation to a PDF into a
        #: download, so `navigate` never saw the candidates at all. Counting
        #: navigations would have left both bounds below asserting nothing.
        self.fetches: list[str] = []
        self.navigations: list[str] = []
        self.page = object()

    def fetch_direct(self, url):
        import time as _time

        from pzi.browser_session import FetchResult

        self.fetches.append(url)
        if self._navigate_seconds:
            _time.sleep(self._navigate_seconds)
        return FetchResult(status=200, content_type="text/html", body=b"<html>")

    def navigate(self, url, **_kwargs):
        self.navigations.append(url)
        return None

    def wait_network_idle(self):
        return None

    def evaluate(self, _script):
        return self._candidates

    def leave_page(self):
        return None


def test_a_hostile_page_cannot_drive_an_unbounded_candidate_loop() -> None:
    """Every candidate costs a navigation with a 30s timeout, and the whole
    loop runs while the server's single browser lock is held — so the page
    being fetched decided how long every other capture waited."""
    from pzi.browser_pdf_hook import MAX_PDF_CANDIDATES, download_pdf

    session = _CandidateSession([f"https://evil.test/{i}.pdf" for i in range(500)])

    download_pdf("https://evil.test/paper", _session=session, _dismiss=lambda _p: None)

    # +1 for the direct attempt on the page URL itself, before any candidate.
    assert len(session.fetches) <= MAX_PDF_CANDIDATES + 1


def test_the_candidate_loop_stops_at_its_deadline() -> None:
    from pzi.browser_pdf_hook import download_pdf

    session = _CandidateSession(
        [f"https://slow.test/{i}.pdf" for i in range(50)], navigate_seconds=0.05
    )

    download_pdf(
        "https://slow.test/paper",
        _session=session,
        _dismiss=lambda _p: None,
        candidate_deadline_seconds=0.2,
    )

    # Far fewer than the 50 offered: the deadline stopped it.
    assert len(session.fetches) < 20


@pytest.mark.parametrize(
    "call",
    ["download_pdf", "_wait_for_verified_pdf"],
)
@pytest.mark.parametrize(
    ("status", "expected"),
    [(200, b"%PDF-1.4 test"), (500, None)],
    ids=["ok", "error-status"],
)
def test_every_navigation_response_is_classified_by_fetchresult(
    call: str, status: int, expected: bytes | None
) -> None:
    """Both navigation sites ask ``FetchResult.is_pdf`` rather than re-deciding.

    "Content-type says PDF and the body starts with %PDF-" was written out at
    three places — ``FetchResult.is_pdf`` plus these two — so an error page
    served as ``application/pdf`` was accepted here and rejected there.
    """
    session = FakeBrowserSession(
        fetch_result=(-1, None, b""),
        goto_results=[make_pdf_response(status=status)],
    )
    if call == "download_pdf":
        result = hook.download_pdf("https://example.com/paper.pdf", _session=session)
    else:
        result = hook._wait_for_verified_pdf(
            cast(Any, session), "https://example.com/paper.pdf", timeout=1
        )

    assert result == expected


# === Paths that claimed browser-test coverage but had none (item 569) ======
#
# Five lines carried `# pragma: no cover — covered by integration/browser
# tests`. Measured with the browser suite actually running and pragma
# exclusions disabled, four of them never executed. None of the four needs a
# browser to reach, so the claim is replaced with a test rather than a better
# excuse.

def test_resolve_pdf_candidate_urls_rejects_a_non_list() -> None:
    """A pure function, and the JS it reads can return anything."""
    assert hook.resolve_pdf_candidate_urls("https://journal.test/a", "not a list") == []
    assert hook.resolve_pdf_candidate_urls("https://journal.test/a", None) == []


def test_download_skips_a_candidate_that_is_not_an_http_url() -> None:
    """Candidates come straight from page JS, unfiltered.

    `session.evaluate` returns whatever the page yields — `javascript:` hrefs,
    `mailto:`, fragments — so the non-http guard is on the ordinary path, not
    an exotic one.
    """
    s = FakeBrowserSession(
        fetch_results=[
            (200, "text/html", b"<html></html>"),          # the landing URL
            (200, "application/pdf", b"%PDF-1.4 linked"),  # the real candidate
        ],
        goto_results=[
            type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"}),
        ],
        evaluate_results=[["javascript:void(0)", "https://journal.test/linked.pdf"]],
    )

    assert hook.download_pdf("https://example.com/paper.pdf", _session=s) == b"%PDF-1.4 linked"


def test_download_moves_on_when_fetching_a_candidate_raises() -> None:
    """One bad candidate must not end the search."""

    class _SecondCandidateWorks(FakeBrowserSession):
        def fetch_direct(self, url):
            if url == "https://journal.test/broken.pdf":
                raise RuntimeError("connection reset")
            return super().fetch_direct(url)

    s = _SecondCandidateWorks(
        fetch_results=[
            (200, "text/html", b"<html></html>"),
            (200, "application/pdf", b"%PDF-1.4 second"),
        ],
        goto_results=[
            type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"}),
        ],
        evaluate_results=[
            ["https://journal.test/broken.pdf", "https://journal.test/good.pdf"]
        ],
    )

    assert hook.download_pdf("https://example.com/paper.pdf", _session=s) == b"%PDF-1.4 second"


def test_download_returns_none_when_the_page_scan_raises() -> None:
    """A failure anywhere in the scan is "no PDF", not a crash out of the hook."""

    class _EvaluateExplodes(FakeBrowserSession):
        def evaluate(self, js):
            raise RuntimeError("execution context was destroyed")

    s = _EvaluateExplodes(
        fetch_results=[(200, "text/html", b"<html></html>")],
        goto_results=[
            type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"}),
        ],
    )

    assert hook.download_pdf("https://example.com/paper.pdf", _session=s) is None


# ---------------------------------------------------------------------------
# Error notes name a failure without its message's text
# ---------------------------------------------------------------------------

TOKEN_URL = "https://pub.example.org/x.pdf?token=SECRET"


class _NavigationRaises(FakeBrowserSession):
    """A session whose requests fail with a message naming the URL, as a
    Playwright error does."""

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._message = message

    def navigate(self, url, *, wait_until="domcontentloaded", timeout=30000):
        raise RuntimeError(self._message)

    def fetch_direct(self, url):
        raise RuntimeError(self._message)


@pytest.mark.parametrize("call", ["discover", "download"])
def test_a_session_note_keeps_no_text_of_the_message(call: str) -> None:
    """`browser session: {exc}` put the URL from the message, token and all,
    in `errors`, which the HTTP API returns as its error text."""
    s = _NavigationRaises(f"Page.goto: net::ERR_ABORTED at {TOKEN_URL}")
    errs: list[str] = []
    if call == "discover":
        hook.discover_pdf_url(TOKEN_URL, _session=s, errors=errs)
    else:
        hook.download_pdf(TOKEN_URL, _session=s, errors=errs)
    assert errs, "no note, so the test proves nothing"
    assert not any("SECRET" in note for note in errs), errs
    assert any("net::ERR_ABORTED" in note for note in errs), errs
    assert any("https://pub.example.org" in note for note in errs), errs


def test_a_candidate_refusal_names_only_the_candidates_origin() -> None:
    """A refused candidate was named in full, token and all."""
    refused = FetchResult(status=-1, content_type=None, body=b"", refused="gzip refused")

    class _Refuses(FakeBrowserSession):
        def fetch_direct(self, url):
            self.calls.append(("fetch_direct", url))
            if url == TOKEN_URL:
                return refused
            return FetchResult(status=200, content_type="text/html", body=b"<html>")

    s = _Refuses(
        goto_results=[make_fake_response(content_type="text/html", body=b"<html>")],
        evaluate_results=[[TOKEN_URL]],
    )
    errs: list[str] = []
    hook.download_pdf("https://journal.test/landing", _session=s, errors=errs)
    assert errs == ["https://pub.example.org: gzip refused"]


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        # A browser reads `\` as `/` in an http(s) URL, so the host ends
        # there; `urlsplit` read `secret.example` (review-nd9 `probe_origin9`).
        ("https://pub.example.org\\@SECRET.example/x.pdf", "https://pub.example.org"),
        ("http:/\\pub.example.org/x.pdf?token=SECRET", "http://pub.example.org"),
        ("http:pub.example.org/x.pdf?token=SECRET", "http://pub.example.org"),
        ("https://user:SECRET@pub.example.org/x.pdf", "https://pub.example.org"),
        ("https://pub.example.org:SECRET@evil.example/x.pdf", "https://evil.example"),
        ("https://pub.exa\tmple.org/x.pdf?SECRET", "https://pub.example.org"),
        ("HTTPS://PUB.example.org:443/x.pdf?t=SECRET", "https://pub.example.org"),
        ("http://127.0.0.1:8080/x?SECRET", "http://127.0.0.1:8080"),
        ("http://[::1]:9/x?SECRET", "http://[::1]:9"),
        # Hosts a browser would decode, map or refuse are not named.
        ("https://pub.example.org%40SECRET/x.pdf", "<url>"),
        ("https://[v1.SECRET]/x.pdf", "<url>"),
        ("https://[::1%25SECRET]/x.pdf", "<url>"),
        ("https://xn--SECRET-abc.example/x.pdf", "<url>"),
        ("https://pub_SECRET.example/x.pdf", "<url>"),
        ("https://ｓｅｃｒｅｔ.example/x.pdf", "<url>"),
        ("https://pub.example.org:443SECRET/x.pdf", "<url>"),
        ("https://pub.example.org:99999/x.pdf", "<url>"),
        ("https://0x7f.1/x.pdf", "<url>"),
        ("//pub.example.org/x.pdf?token=SECRET", "<url>"),
        ("file:///home/SECRET", "<url>"),
    ],
)
def test_url_origin_reads_the_host_as_a_browser_does(url: str, origin: str) -> None:
    assert hook._url_origin(url) == origin


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Page.goto: net::ERR_ABORTED at https://pub.example.org/x?t=SECRET", "net::ERR_ABORTED"),
        ("Page.goto: NS_BINDING_ABORTED", "NS_BINDING_ABORTED"),
        ("Page.goto: net::ERR_UNSAFE_PORT at http://127.0.0.1:9/?t=SECRET", "net::ERR_UNSAFE_PORT"),
        ("Page.goto: NS_ERROR_PORT_ACCESS_NOT_ALLOWED", "NS_ERROR_PORT_ACCESS_NOT_ALLOWED"),
        ("Page.goto: Timeout 30000ms exceeded.", "Timeout"),
        (
            'Page.goto: Navigation to "https://x/?t=SECRET" is interrupted by another navigation',
            "interrupted by another navigation",
        ),
        ("evaluate did not answer within 5.0 s", "Timeout"),
        ("BrowserSession is closed", "browser session is closed"),
        # Text a page's script threw is never a code (review-nd9 `probe_pagecode9`).
        ("Page.evaluate: NS_PAGETEXT_42", "the page's script threw"),
        ("Page.evaluate: net::ERR_ABORTED SECRET", "the page's script threw"),
        ("Page.evaluate: Timeout 1ms exceeded SECRET", "the page's script threw"),
        # Code-shaped text that is no known code.
        ("Page.goto: NS_SECRET_TOKEN", "unrecognised"),
        ("net::ERR_SECRET", "unrecognised"),
        ("Page.goto: TimeoutSECRET", "unrecognised"),
        ("refusing to navigate to a non-public URL: https://x/?t=SECRET", "non-public URL"),
    ],
)
def test_a_failure_is_described_by_a_code_or_a_fixed_reason(message: str, reason: str) -> None:
    note = hook._describe_failure(RuntimeError(message), TOKEN_URL)
    assert note == f"RuntimeError: {reason} at https://pub.example.org"


# ---------------------------------------------------------------------------
# A refusal is recorded with its reason, apart from a dead session
# ---------------------------------------------------------------------------


def test_download_pdf_records_a_guard_refusal_of_the_navigation() -> None:
    """`SsrfBlocked` left `errors` empty, so a refusal read as "no PDF"."""
    from pzi.safe_http import SsrfBlocked

    url = "https://journal.test/rebind"
    s = _raising_session(
        start_url="https://journal.test/previous",
        lands_on=url,
        error=SsrfBlocked("browser landed on a non-public URL"),
    )
    errors: list[str] = []
    refusals: list[str] = []

    assert hook.download_pdf(url, _session=s, errors=errors, refusals=refusals) is None
    assert errors == refusals
    assert len(errors) == 1 and "non-public URL" in errors[0], errors


def test_download_pdf_records_a_guard_refusal_in_the_challenge_loop() -> None:
    """The challenge loop's own navigation can land on a refused URL too."""
    from pzi.safe_http import SsrfBlocked
    from tests.fake_session import FakeBrowserSession

    html = type("R", (), {
        "status": 200, "headers": {"content-type": "text/html"}, "body": lambda: b"<html>",
    })

    class Challenged(FakeBrowserSession):
        navigations = 0

        def navigate(self, url, **kwargs):
            self.calls.append(("navigate", url))
            Challenged.navigations += 1
            if Challenged.navigations == 1:
                return html
            raise SsrfBlocked("challenge redirected to a non-public URL")

    errors: list[str] = []
    refusals: list[str] = []
    result = hook.download_pdf(
        "https://journal.test/challenge", _session=Challenged(), challenge_timeout=1,
        errors=errors, refusals=refusals,
    )

    assert result is None
    assert any("challenge redirected" in e for e in refusals), (errors, refusals)


def test_download_pdf_keeps_a_dead_session_out_of_the_refusals() -> None:
    from tests.fake_session import FakeBrowserSession

    s = FakeBrowserSession(fetch_result=(-1, None, b""))
    s.close()
    errors: list[str] = []
    refusals: list[str] = []

    assert hook.download_pdf(
        "https://example.com/paper.pdf", _session=s, errors=errors, refusals=refusals
    ) is None
    assert errors and "browser session is closed" in errors[0]
    assert refusals == []


def test_download_pdf_records_a_refused_body_as_a_refusal() -> None:
    from tests.fake_session import FakeBrowserSession

    class Compressed(FakeBrowserSession):
        def fetch_direct(self, url):
            self.calls.append(("fetch_direct", url))
            return FetchResult(
                status=-1, content_type=None, body=b"", refused="gzip-encoded PDF refused"
            )

    errors: list[str] = []
    refusals: list[str] = []
    hook.download_pdf(
        "https://journal.test/a.pdf", _session=Compressed(), errors=errors, refusals=refusals
    )

    assert "gzip-encoded PDF refused" in refusals
    assert errors == refusals


def test_download_pdf_names_a_guard_refusal_in_errors() -> None:
    """The caller that passes only `errors` still learns why nothing came back."""
    from pzi.safe_http import SsrfBlocked

    url = "https://journal.test/rebind"
    s = _raising_session(
        start_url="https://journal.test/previous",
        lands_on=url,
        error=SsrfBlocked("browser landed on a non-public URL"),
    )
    errors: list[str] = []

    assert hook.download_pdf(url, _session=s, errors=errors) is None
    assert errors and "non-public URL" in errors[0], errors


def test_a_recorded_landing_refusal_leaves_out_the_landed_urls_query() -> None:
    """The landed URL is the redirect's choice, and its query can be an SSO ticket.

    The guard's reason named it in full, and the reason is recorded, returned
    to the CLI in the server's error body and printed.
    """
    from pzi.browser_session import BrowserSession

    landed = "http://10.1.2.3/sso?ticket=ST-SECRET123#frag"

    class FakeRequest:
        def get(self, *_args, **_kwargs):
            raise OSError("no direct fetch")

    class FakePage:
        url = "about:blank"
        request = FakeRequest()

        def goto(self, url, **_kwargs):
            self.url = landed
            return None

    session = BrowserSession(
        playwright="pw", browser_ref="br", page=FakePage(),
        url_allowed=lambda url: "10.1.2.3" not in url,
    )
    errors: list[str] = []
    refusals: list[str] = []

    assert hook.download_pdf(
        "https://journal.test/p.pdf", _session=session, errors=errors, refusals=refusals
    ) is None

    assert len(refusals) == 1, errors
    assert "http://10.1.2.3/sso" in refusals[0], refusals
    assert "ST-SECRET123" not in " ".join(errors), errors
    assert "frag" not in refusals[0], refusals


@pytest.mark.parametrize(
    ("landed", "named", "hidden"),
    [
        ("http://u:pw@10.1.2.3/login;jsessionid=ABC123?t=1#f",
         "http://10.1.2.3/login", ("pw", "u:", "jsessionid", "ABC123")),
        ("http://u:pw@[fd00::1]:8080/a;sid=S1/b?t=1",
         "http://[fd00::1]:8080/a", ("pw", "sid", "S1")),
        ("http://u:pw@[::1/x;sid=S1?y=1", "http://[::1/x", ("pw", "sid", "S1")),
    ],
    ids=["userinfo-and-jsessionid", "ipv6-port", "unparseable"],
)
def test_a_landing_refusal_leaves_out_userinfo_and_path_parameters(
    landed: str, named: str, hidden: tuple[str, ...]
) -> None:
    """Userinfo (`u:pw@`) and a path parameter (`;jsessionid=`) are credentials too.

    The refusal reason is recorded, returned in the server's error body and
    printed. Cutting only the query and fragment left both in it.
    """
    from pzi.browser_session import BrowserSession
    from pzi.safe_http import SsrfBlocked

    class FakePage:
        url = "about:blank"

        def goto(self, url, **_kwargs):
            self.url = landed
            return None

    session = BrowserSession(
        playwright="pw", browser_ref="br", page=FakePage(),
        url_allowed=lambda url: "journal.test" in url,
    )

    with pytest.raises(SsrfBlocked) as caught:
        session.navigate("https://journal.test/p.pdf")

    reason = str(caught.value)
    assert reason == f"<urlopen error browser landed on a non-public URL: {named}>"
    for secret in hidden:
        assert secret not in reason, reason


def test_a_discovery_cut_by_the_click_is_noted(monkeypatch) -> None:
    """The click's download wait used up the deadline and nothing was found:
    `errors` stayed empty, so the cut read as "no PDF here" (review-st2
    `cut_after_click_no_pdf`; intermittent in the Firefox deadline test)."""
    import time as _time

    monkeypatch.setattr(hook, "DISCOVERY_TIMEOUT_SECONDS", 0.5)

    def slow_click(page) -> bool:
        _time.sleep(0.7)
        return True

    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], evaluate_results=[[]])
    errors: list[str] = []
    result = hook.discover_pdf_url(
        "https://example.com/landing",
        _session=s,
        _click=slow_click,
        _resolve=lambda url, c: [],
        errors=errors,
    )
    assert result is None
    assert errors == ["discovery cut at its deadline: TimeoutError: Timeout at https://example.com"]


def test_a_click_that_lands_on_the_pdf_late_is_a_find(monkeypatch) -> None:
    """The click used up the deadline but navigated inline to the PDF: the
    post-click cut returned None and noted a cut (review-w4 of `160e12a`)."""
    import time as _time

    monkeypatch.setattr(hook, "DISCOVERY_TIMEOUT_SECONDS", 0.5)

    def slow_click(page) -> bool:
        _time.sleep(0.7)
        return True

    s = FakeBrowserSession(
        goto_results=[_LOADED_PAGE],
        url="https://journal.test/paper.pdf",
        evaluate_results=[[]],
    )
    errors: list[str] = []
    result = hook.discover_pdf_url(
        "https://example.com/landing",
        _session=s,
        _click=slow_click,
        _resolve=lambda url, c: [],
        errors=errors,
    )
    assert result == "https://journal.test/paper.pdf"
    assert errors == []


def test_a_page_with_nothing_to_click_is_no_pdf_not_a_cut(monkeypatch) -> None:
    """Nothing clicked, and the download wait ran out the deadline: no later
    step was skipped, so this is "no PDF here", not a cut that the route
    answers with 503 "retry later" (review-w4b `probe_branches` case A)."""
    import time as _time

    monkeypatch.setattr(hook, "DISCOVERY_TIMEOUT_SECONDS", 0.5)

    def no_click(page) -> bool:
        _time.sleep(0.7)
        return False

    s = FakeBrowserSession(goto_results=[_LOADED_PAGE], evaluate_results=[[]])
    errors: list[str] = []
    result = hook.discover_pdf_url(
        "https://example.com/landing",
        _session=s,
        _click=no_click,
        _resolve=lambda url, c: [],
        errors=errors,
    )
    assert result is None
    assert errors == []
