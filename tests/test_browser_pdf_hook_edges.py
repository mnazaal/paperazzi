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


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        content_type: str = "application/pdf",
        body: bytes = b"%PDF-x",
    ) -> None:
        self.status = status
        self.headers = {"content-type": content_type}
        self._body = body

    def body(self) -> bytes:
        return self._body


class FakeRequest:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def get(self, url: str):
        if self.error:
            raise self.error
        return self.response


class FakeBrowserPage:
    def __init__(self) -> None:
        self.url = "https://journal.test/article"
        self.request = FakeRequest(FakeResponse())
        self.goto_calls: list[str] = []
        self.evaluate_results: list[object] = []
        self.goto_responses: list[object] = []
        self.clickable_selectors: set[str] = set()

    def goto(self, url: str, **kwargs):
        self.goto_calls.append(url)
        if self.goto_responses:
            return self.goto_responses.pop(0)
        return None

    def wait_for_load_state(self, *args, **kwargs) -> None:
        return None

    def evaluate(self, script: str):
        return self.evaluate_results.pop(0) if self.evaluate_results else []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


def test_discover_pdf_url_returns_post_click_pdf_url(monkeypatch) -> None:
    page = FakeBrowserPage()
    page.url = "https://journal.test/download.pdf"
    page.evaluate_results = [[]]
    monkeypatch.setattr(
        hook,
        "_launch_browser",
        lambda browser, profile_path: (object(), object(), page),
    )
    monkeypatch.setattr(hook, "_close_browser", lambda playwright, browser_ref, page: None)
    monkeypatch.setattr(hook, "_click_downloadish_links", lambda page: True)

    assert hook.discover_pdf_url("https://journal.test/article") == "https://journal.test/download.pdf"


def test_discover_pdf_url_returns_post_click_candidate(monkeypatch) -> None:
    page = FakeBrowserPage()
    page.evaluate_results = [[], ["/reader/download?id=1"]]
    monkeypatch.setattr(
        hook,
        "_launch_browser",
        lambda browser, profile_path: (object(), object(), page),
    )
    monkeypatch.setattr(hook, "_close_browser", lambda playwright, browser_ref, page: None)
    monkeypatch.setattr(hook, "_click_downloadish_links", lambda page: True)

    assert hook.discover_pdf_url("https://journal.test/article") == (
        "https://journal.test/reader/download?id=1"
    )


def test_download_pdf_uses_direct_request_when_pdf(monkeypatch) -> None:
    page = FakeBrowserPage()
    page.request = FakeRequest(FakeResponse(body=b"%PDF-direct"))
    monkeypatch.setattr(
        hook,
        "_launch_browser",
        lambda browser, profile_path: (object(), object(), page),
    )
    monkeypatch.setattr(hook, "_close_browser", lambda playwright, browser_ref, page: None)

    assert hook.download_pdf("https://journal.test/paper.pdf") == b"%PDF-direct"
    assert page.goto_calls == []


def test_download_pdf_follows_candidate_links_after_html_page(monkeypatch) -> None:
    page = FakeBrowserPage()
    page.request = FakeRequest(error=RuntimeError("request unavailable"))
    page.goto_responses = [
        FakeResponse(content_type="text/html", body=b"<html>"),
        FakeResponse(body=b"%PDF-linked"),
    ]
    page.evaluate_results = [["https://journal.test/linked.pdf"]]
    monkeypatch.setattr(
        hook,
        "_launch_browser",
        lambda browser, profile_path: (object(), object(), page),
    )
    monkeypatch.setattr(hook, "_close_browser", lambda playwright, browser_ref, page: None)

    assert hook.download_pdf("https://journal.test/article") == b"%PDF-linked"
    assert page.goto_calls == ["https://journal.test/article", "https://journal.test/linked.pdf"]
