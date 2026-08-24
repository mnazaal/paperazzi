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
from tests.fake_session import FakeBrowserSession, make_pdf_response


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
        lambda page_url, *, browser, profile_path, headless=True: f"{page_url}/paper.pdf"
        if browser == "firefox" and profile_path == "prof"
        else None,
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

def test_discover_no_candidates() -> None:
    s = FakeBrowserSession(evaluate_results=[[]])
    result = hook.discover_pdf_url("https://example.com", _session=s)
    assert result is None


def test_discover_candidates_found() -> None:
    s = FakeBrowserSession(evaluate_results=[["/paper.pdf"]])
    result = hook.discover_pdf_url(
        "https://example.com",
        _session=s,
        _resolve=lambda page_url, c: ["https://example.com/paper.pdf"],
    )
    assert result == "https://example.com/paper.pdf"


def test_discover_post_click_pdf() -> None:
    s = FakeBrowserSession(
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
    s = FakeBrowserSession(
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
    s = FakeBrowserSession(
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
