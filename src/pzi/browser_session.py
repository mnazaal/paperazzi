"""Browser session abstraction — unified Playwright lifecycle management.

A BrowserSession wraps the Playwright (playwright instance, browser/context, page)
triple into a single typed object with a context-manager-based lifecycle.

Usage:
    with open_browser_session(browser="chromium") as session:
        session.navigate("https://example.com")
        data = session.evaluate("document.title")
        pdf = session.fetch_direct("https://example.com/paper.pdf")
    # automatically closed even on exception
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


def browser_launch_options(browser: str) -> dict[str, Any]:
    """Return Playwright launch kwargs for browser name."""
    options: dict[str, Any] = {"headless": True}
    if browser == "firefox":
        options["firefox_user_prefs"] = {
            "browser.download.folderList": 2,
            "browser.download.manager.showWhenStarting": False,
            "pdfjs.disabled": True,
        }
    return options


@dataclass
class BrowserSession:
    """Unified browser session wrapping Playwright lifecycle.

    All interaction methods delegate to the underlying Playwright page
    so callers never touch playwright/browser/context objects directly.
    """

    playwright: Any = field(repr=False)
    browser_ref: Any = field(repr=False)
    page: Any = field(repr=False)
    _closed: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout: int = 30000,
    ) -> Any:
        """Navigate to URL, returning the Playwright response object."""
        self._check_open()
        return self.page.goto(url, wait_until=wait_until, timeout=timeout)

    def current_url(self) -> str:
        """Return the current page URL."""
        self._check_open()
        return self.page.url

    # ------------------------------------------------------------------
    # JavaScript evaluation
    # ------------------------------------------------------------------

    def evaluate(self, js: str) -> Any:
        """Evaluate JavaScript in the page and return the result."""
        self._check_open()
        return self.page.evaluate(js)

    # ------------------------------------------------------------------
    # Direct HTTP requests (through browser's network stack)
    # ------------------------------------------------------------------

    def fetch_direct(self, url: str) -> FetchResult:
        """Perform a direct HTTP GET through the browser's request context.

        Returns a FetchResult with status, content_type, and body bytes.
        Does NOT navigate the page — uses the browser's HTTP stack directly.
        """
        self._check_open()
        try:
            response = self.page.request.get(url)
            ct = response.headers.get("content-type", "")
            body = response.body() if response.status == 200 else b""
            return FetchResult(
                status=response.status,
                content_type=ct,
                body=body,
            )
        except Exception:
            return FetchResult(status=-1, content_type=None, body=b"")

    # ------------------------------------------------------------------
    # DOM interaction
    # ------------------------------------------------------------------

    def click_first(self, selector: str, *, timeout: int = 1000) -> bool:
        """Click the first element matching *selector*.  Returns True on success."""
        self._check_open()
        try:
            self.page.locator(selector).first.click(timeout=timeout)
            return True
        except Exception:
            return False

    def try_click_first(self, selectors: list[str], *, timeout: int = 1000) -> bool:
        """Try each selector in order; return True as soon as one succeeds."""
        for selector in selectors:
            if self.click_first(selector, timeout=timeout):
                return True
        return False

    # ------------------------------------------------------------------
    # Wait helpers
    # ------------------------------------------------------------------

    def wait_network_idle(self, *, timeout: int = 5000) -> None:
        """Wait for network idle, silently swallowing timeout."""
        self._check_open()
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except (ImportError, Exception):
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close browser resources.  Idempotent — safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        try:
            if isinstance(self.browser_ref, tuple):
                browser, context = self.browser_ref
                context.close()
                browser.close()
            else:
                self.browser_ref.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("BrowserSession is closed")


@dataclass
class FetchResult:
    """Result of a direct HTTP request through the browser's network stack."""
    status: int
    content_type: str | None
    body: bytes

    def is_pdf(self) -> bool:
        """True if the response looks like a PDF."""
        if self.status != 200:
            return False
        if self.content_type and "application/pdf" in self.content_type:
            return self.body.startswith(b"%PDF-")
        return False


# ------------------------------------------------------------------
# Context manager entry point
# ------------------------------------------------------------------


def _launch_browser(browser: str, profile_path: str | None) -> BrowserSession:
    """Launch a browser and return a BrowserSession."""
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    options = browser_launch_options(browser)

    if profile_path:
        profile = Path(profile_path).expanduser()
        if browser == "firefox":
            ctx = playwright.firefox.launch_persistent_context(
                user_data_dir=str(profile), **options
            )
        else:
            ctx = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), **options
            )
        page = ctx.new_page()
        return BrowserSession(playwright=playwright, browser_ref=ctx, page=page)

    # Headless — no persistent profile
    if browser == "firefox":
        browser_instance = playwright.firefox.launch(**options)
        context = browser_instance.new_context()
    else:
        browser_instance = playwright.chromium.launch(**options)
        context = browser_instance.new_context()
    page = context.new_page()
    return BrowserSession(
        playwright=playwright,
        browser_ref=(browser_instance, context),
        page=page,
    )


@contextmanager
def open_browser_session(
    browser: str = "chromium",
    profile_path: str | None = None,
) -> Iterator[BrowserSession]:
    """Context manager: guaranteed cleanup even on exception.

    Usage:
        with open_browser_session() as session:
            session.navigate("https://example.com")
            ...
    """
    session = _launch_browser(browser, profile_path)
    try:
        yield session
    finally:
        session.close()
