import base64
import io
import json
import subprocess
import sys
import types

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
        fetch_result=(-1, None, b""),
        goto_results=[
            type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"}),
            type("F", (), {"headers": {"content-type": "application/pdf"}, "body": lambda: b"%PDF-1.4 linked"}),
        ],
        evaluate_results=[["https://journal.test/linked.pdf"]],
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 linked"


def test_download_candidate_non_string_skipped() -> None:
    html = type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"})
    s = FakeBrowserSession(
        fetch_result=(-1, None, b""),
        goto_results=[html],
        evaluate_results=[[None, 123, "https://journal.test/paper.pdf"]],
    )
    # second goto for the valid candidate
    s._gotos.append(
        type("F", (), {"headers": {"content-type": "application/pdf"}, "body": lambda: b"%PDF-1.4 found"})
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


def test_close_browser_handles_headless_and_persistent_refs() -> None:
    events: list[str] = []

    class Closable:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(f"close:{self.name}")

    class Playwright:
        def stop(self) -> None:
            events.append("stop")

    hook._close_browser(Playwright(), (Closable("browser"), Closable("context")), object())
    hook._close_browser(Playwright(), Closable("persistent"), object())

    assert events == ["close:context", "close:browser", "stop", "close:persistent", "stop"]


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
        fetch_result=(-1, None, b""),
        goto_results=[
            type("F", (), {
                "headers": {"content-type": "text/html"},
                "body": lambda: b"<html>",
            }),
            type("F", (), {
                "headers": {"content-type": "application/pdf"},
                "body": lambda: b"%PDF-linked",
            }),
        ],
        evaluate_results=[["https://journal.test/linked.pdf"]],
    )
    result = hook.download_pdf("https://journal.test/article", _session=s)
    assert result == b"%PDF-linked"
