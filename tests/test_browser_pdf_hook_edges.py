import subprocess
import sys
import types

from pzi import browser_pdf_hook as hook


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
    assert events == ["install:chromium"]


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


from tests.fake_session import FakeBrowserSession
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
