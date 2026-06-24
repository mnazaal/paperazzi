"""Persistent browser session manager for server-mode operation.

Provides a singleton-style BrowserSessionManager that lazily launches
a Playwright-based BrowserSession and keeps it alive across requests.
Delegates to browser_pdf_hook functions with session injection so no
subprocess is needed per request.

Usage:
    manager = BrowserSessionManager(browser="chromium", profile_path="...")
    pdf_url = manager.discover_pdf_url("https://...")
    pdf_bytes = manager.download_pdf_bytes("https://...pdf")
    manager.close()
"""

from __future__ import annotations

import threading
import time
from typing import Any


class BrowserSessionManager:
    """Persistent browser session for PDF discovery and download.

    Lazily launches the browser on first request.  Thread-safe: the underlying
    Playwright sync page is not safe for concurrent use, so each discover/
    download call holds the lock for its whole duration — browser work is
    single-flighted across the ``ThreadingHTTPServer``'s request threads.  The
    lock is reentrant so a call may invoke ``ensure_session`` while holding it.
    Crash-tolerant: if the underlying session dies, ensure_session() launches a
    fresh one.
    """

    def __init__(
        self,
        *,
        browser: str = "chromium",
        profile_path: str | None = None,
        headless: bool = True,
    ) -> None:
        self._browser = browser
        self._profile_path = profile_path
        self._headless = headless
        self._lock = threading.RLock()
        self._last_used: float = 0.0
        self._session: Any = None

    # -- public interface -------------------------------------------------

    def ensure_session(self) -> Any:
        """Return a live BrowserSession, launching one if necessary.

        Thread-safe.  Re-launches on crash.
        """
        with self._lock:
            if self._session is not None:
                try:
                    self._session._check_open()
                    self._last_used = time.monotonic()
                    return self._session
                except RuntimeError:
                    # session is closed / crashed — clean up and re-launch
                    try:
                        self._session.close()
                    except Exception:
                        pass
                    self._session = None
            self._session = self._launch()
            self._last_used = time.monotonic()
            return self._session

    def discover_pdf_url(
        self,
        page_url: str,
        *,
        doi: str | None = None,
    ) -> str | None:
        """Discover PDF URL from a page using the persistent browser session."""
        from pzi.browser_pdf_hook import discover_pdf_url as _discover

        # Hold the lock for the whole operation: the shared Playwright page
        # cannot be driven from two threads at once.
        with self._lock:
            session = self.ensure_session()
            return _discover(
                page_url,
                browser=self._browser,
                _session=session,
                headless=self._headless,
            )

    def download_pdf_bytes(self, pdf_url: str) -> bytes | None:
        """Download PDF bytes using the persistent browser session."""
        from pzi.browser_pdf_hook import download_pdf as _download

        with self._lock:
            session = self.ensure_session()
            return _download(
                pdf_url,
                browser=self._browser,
                _session=session,
                headless=self._headless,
            )

    def close(self) -> None:
        """Close the browser session.  Idempotent."""
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None

    def idle_seconds(self) -> float:
        """Seconds since the last ensure_session() call."""
        return time.monotonic() - self._last_used

    # -- internal ---------------------------------------------------------

    def _launch(self) -> Any:
        """Launch a fresh BrowserSession (called under lock)."""
        from pzi.browser_session import _launch_browser

        return _launch_browser(
            self._browser,
            self._profile_path,
            headless=self._headless,
        )
