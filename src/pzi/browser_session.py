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

import contextlib
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _clone_chrome_profile(profile: Path) -> Path:
    """Clone a Chrome user-data dir to a temporary location.

    Chrome refuses to enable remote debugging on its *default* user-data
    directory. Copying to a temp dir satisfies the "non-default" requirement
    while preserving cookies and session state.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="pzi-chrome-"))
    # Tighten permissions so other users on the system cannot read
    # the cloned profile (which contains cookies and session tokens).
    os.chmod(temp_dir, 0o700)

    def _ignore(_dir: str, contents: list[str]) -> list[str]:
        return [
            c
            for c in contents
            if c
            in {
                "Cache",
                "Code Cache",
                "GPUCache",
                "Service Worker",
                "SingletonLock",
                "SingletonSocket",
                "SingletonCookie",
                "lockfile",
            }
        ]

    shutil.copytree(profile, temp_dir, dirs_exist_ok=True, ignore=_ignore)
    return temp_dir


def browser_launch_options(browser: str, *, headless: bool = True) -> dict[str, Any]:
    """Return Playwright launch kwargs for browser name."""
    options: dict[str, Any] = {"headless": headless}
    if browser == "firefox":
        options["firefox_user_prefs"] = {
            "browser.download.folderList": 2,
            "browser.download.manager.showWhenStarting": False,
            "pdfjs.disabled": True,
        }
    return options


def _default_url_allowed(url: str) -> bool:
    """The production predicate: a public http(s) destination and nothing else."""
    from pzi.url_safety import safe_public_http_url

    return safe_public_http_url(url)


@dataclass
class BrowserSession:
    """Unified browser session wrapping Playwright lifecycle.

    All interaction methods delegate to the underlying Playwright page
    so callers never touch playwright/browser/context objects directly.
    """

    playwright: Any = field(repr=False)
    browser_ref: Any = field(repr=False)
    page: Any = field(repr=False)
    #: What counts as a destination this session may reach. Injected rather
    #: than read from config or the environment: a switch that turns SSRF
    #: protection off would be reachable from `config.toml` and from the HTTP
    #: API, which is precisely what the guard exists to prevent. The only
    #: caller that overrides it is the test suite, whose fixture servers are on
    #: loopback — and a test that has to reach into the process to change it
    #: cannot be triggered from outside.
    url_allowed: Callable[[str], bool] = field(default=_default_url_allowed, repr=False)
    _closed: bool = field(default=False, init=False)
    _temp_profile: Path | None = field(default=None, init=False, repr=False)

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
        """Navigate to URL, returning the Playwright response object.

        The *landing* URL is checked as well as the one asked for: the guard
        installed by :func:`install_request_guard` refuses each hop, and this
        catches anything that still arrives somewhere non-public (a
        ``file:``/``about:`` redirect, or a guard that failed to install).
        """
        self._check_open()
        response = self.page.goto(url, wait_until=wait_until, timeout=timeout)
        self._reject_non_public_landing()
        return response

    def _reject_non_public_landing(self) -> None:
        from pzi.safe_http import SsrfBlocked

        landed = ""
        with contextlib.suppress(Exception):
            landed = self.page.url or ""
        if landed and not self.url_allowed(landed):
            raise SsrfBlocked(f"browser landed on a non-public URL: {landed}")

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
        if not self.url_allowed(url):
            return FetchResult(status=-1, content_type=None, body=b"")
        try:
            response = self.page.request.get(url)
            final_url = getattr(response, "url", url) or url
            if not self.url_allowed(final_url):
                # Redirected somewhere private. `page.request` does not go
                # through the page's route handler, so this is its guard.
                return FetchResult(status=-1, content_type=None, body=b"")
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
    # Wait helpers
    # ------------------------------------------------------------------

    def wait_network_idle(self, *, timeout: int = 5000) -> None:
        """Wait for network idle, silently swallowing timeout."""
        self._check_open()
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
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
        if self._temp_profile is not None:
            try:
                shutil.rmtree(self._temp_profile, ignore_errors=True)
            except Exception:
                pass

    def _check_open(self) -> None:
        """Raise unless this session can still be used.

        `_closed` alone was not enough: it is set only by `close()`, so a
        browser killed underneath us — OOM, a crash, the user quitting it —
        left the flag False and the dead session was handed back forever.
        `BrowserSessionManager` treats the `RuntimeError` as "relaunch", which
        its docstring already promised, so the only thing missing was noticing.
        """
        if self._closed:
            raise RuntimeError("BrowserSession is closed")
        if not self._browser_is_connected():
            raise RuntimeError("BrowserSession's browser is no longer running")

    def _browser_is_connected(self) -> bool:
        """Whether Playwright still has a live connection to the browser.

        A local boolean on the Playwright object, so it is cheap enough for the
        per-operation check. Anything that cannot answer is treated as alive:
        this is a liveness probe, not an excuse to refuse work.
        """
        browser = self.browser_ref
        if isinstance(browser, tuple):
            browser = browser[0]
        is_connected = getattr(browser, "is_connected", None)
        if not callable(is_connected):
            return True
        try:
            return bool(is_connected())
        except Exception:
            return False


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


def install_request_guard(
    page: Any, url_allowed: Callable[[str], bool] = _default_url_allowed
) -> None:
    """Refuse browser requests to non-public destinations.

    ``safe_http`` protects everything pzi fetches itself, but a Playwright page
    fetches through the *browser's* stack: it follows redirects and resolves DNS
    on its own, so validating only the URL handed in leaves
    ``https://public.example/x`` → ``http://127.0.0.1:8765/`` (or a DNS name
    that resolves to a private address) entirely unguarded. Routing every
    request through the same public-URL predicate closes redirect and
    rebinding, because each hop is a request of its own.

    Best effort by construction: if Playwright cannot install the route (an
    older build, a closed page) the caller still has the entry-point check.
    """
    def _guard(route: Any, request: Any) -> None:
        url = getattr(request, "url", "") or ""
        try:
            if url_allowed(url):
                route.continue_()
                return
            route.abort()
        except Exception:  # pragma: no cover — Playwright teardown races
            with contextlib.suppress(Exception):
                route.abort()

    with contextlib.suppress(Exception):
        page.route("**/*", _guard)


def _launch_browser(
    browser: str,
    profile_path: str | None,
    *,
    headless: bool = True,
    url_allowed: Callable[[str], bool] = _default_url_allowed,
) -> BrowserSession:
    """Launch a browser and return a BrowserSession."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover — playwright installed in dev/test
        raise ImportError(
            "playwright is required for browser PDF features. "
            "Install the optional extra: pip install 'paperazzi[playwright]' "
            "(or pipx install 'paperazzi[playwright]'), then: playwright install"
        )

    playwright = sync_playwright().start()
    options = browser_launch_options(browser, headless=headless)

    temp_profile: Path | None = None
    if profile_path:
        profile = Path(profile_path).expanduser()
        # Everything past the clone is wrapped: the clone is a full copy of the
        # user's Chrome profile, cookie database included, and a failed launch
        # used to abandon it in $TMPDIR where nothing ever removed it — along
        # with the Playwright driver process it had already started.
        try:
            if browser in ("chrome", "chromium"):
                # Chrome refuses remote debugging on its default user-data dir.
                # Clone to a temp location to satisfy the "non-default" requirement.
                temp_profile = _clone_chrome_profile(profile)
                profile = temp_profile
            if browser == "firefox":
                ctx = playwright.firefox.launch_persistent_context(
                    user_data_dir=str(profile), **options
                )
            elif browser == "chrome":
                ctx = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile), channel="chrome", **options
                )
            else:
                ctx = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile), **options
                )
            page = ctx.new_page()
        except BaseException:
            if temp_profile is not None:
                shutil.rmtree(temp_profile, ignore_errors=True)
            with contextlib.suppress(Exception):
                playwright.stop()
            raise
        install_request_guard(page, url_allowed)
        session = BrowserSession(
            playwright=playwright, browser_ref=ctx, page=page, url_allowed=url_allowed,
        )
        session._temp_profile = temp_profile
        return session

    # Headless — no persistent profile. Wrapped for the same reason the
    # `profile_path` branch above is: `sync_playwright().start()` has already
    # spawned the driver process, and a launch that raises here left it running
    # with nothing holding a reference. In server mode `ensure_session`
    # re-launches per request, so a browser that cannot start leaked one driver
    # per attempt until the machine ran out.
    try:
        if browser == "firefox":
            browser_instance = playwright.firefox.launch(**options)
            context = browser_instance.new_context()
        elif browser == "chrome":
            browser_instance = playwright.chromium.launch(channel="chrome", **options)
            context = browser_instance.new_context()
        else:
            browser_instance = playwright.chromium.launch(**options)
            context = browser_instance.new_context()
        page = context.new_page()
    except BaseException:
        with contextlib.suppress(Exception):
            playwright.stop()
        raise
    install_request_guard(page, url_allowed)
    return BrowserSession(
        playwright=playwright,
        browser_ref=(browser_instance, context),
        page=page,
        url_allowed=url_allowed,
    )


@contextmanager
def open_browser_session(
    browser: str = "chromium",
    profile_path: str | None = None,
    *,
    headless: bool = True,
    url_allowed: Callable[[str], bool] = _default_url_allowed,
) -> Iterator[BrowserSession]:
    """Context manager: guaranteed cleanup even on exception.

    Usage:
        with open_browser_session() as session:
            session.navigate("https://example.com")
            ...
    """
    session = _launch_browser(
        browser, profile_path, headless=headless, url_allowed=url_allowed
    )
    try:
        yield session
    finally:
        session.close()
