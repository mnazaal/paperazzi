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
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class BrowserSessionManager:
    """Persistent browser session for PDF discovery and download.

    Lazily launches the browser on first request.  Crash-tolerant: if the
    underlying session dies, ``ensure_session`` launches a fresh one.

    **Thread handling, and why there are two mechanisms.**  Playwright's sync
    API is not merely unsafe for concurrent use — it binds its objects to the
    thread that created them, and a call from any other thread fails outright
    with a greenlet error.  Serialising the calls is therefore not enough: the
    ``ThreadingHTTPServer`` hands each request to a different thread, so a
    session launched on the first request's thread was unusable from the
    second's, and the server answered exactly one browser request per process.

    So the session is owned by a single dedicated worker thread and every
    Playwright touch — launch, liveness check, discover, download, close — is
    marshalled to it.  The reentrant lock is kept on top of that, because it
    holds for the whole of a public call and so keeps one request's discover
    from interleaving with another's download; the worker thread alone would
    serialise the individual touches but not the operation.
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
        self._session: Any = None
        #: Single worker thread owning every Playwright object.  Created on
        #: first use and torn down by ``close``, so a manager that never
        #: launches a browser never starts a thread.
        self._executor: ThreadPoolExecutor | None = None

    # -- public interface -------------------------------------------------

    def ensure_session(self) -> Any:
        """Return a live BrowserSession, launching one if necessary.

        Thread-safe.  Re-launches on crash.  The session is created on, and
        handed back from, the owner thread; callers only ever hold a reference.
        """
        with self._lock:
            return self._run(self._ensure_session_on_owner_thread)

    def discover_pdf_url(
        self, page_url: str, *, errors: list[str] | None = None
    ) -> str | None:
        """Discover PDF URL from a page using the persistent browser session.

        There is deliberately no `doi` parameter. One used to be accepted and
        silently discarded — `browser_pdf_hook.discover_pdf_url` has nothing to
        forward it to — so the extension's DOI hint went nowhere while the
        signature implied it was used. Refusing it is honest; wiring it through
        discovery would be a feature.

        *errors* collects why the browser stage produced nothing, so a session
        that crashed is answerable with 503 instead of a 200 saying the page
        has no PDF. `_handle_browser_discover_post` passes it only when this
        method accepts it, so without this parameter the whole G1 fix silently
        no-opped on the persistent-session path and fired only for the
        subprocess one — the fix landing at one call site while its sibling
        kept the bug, which is this project's most common defect by far.
        """
        from pzi.browser_pdf_hook import discover_pdf_url as _discover

        def work() -> str | None:
            # Runs on the owner thread, so it calls the session helper rather
            # than the public `ensure_session` — submitting from inside the
            # single worker would deadlock on itself.
            session = self._ensure_session_on_owner_thread()
            return _discover(
                page_url,
                browser=self._browser,
                _session=session,
                headless=self._headless,
                errors=errors,
            )

        # The lock spans the whole operation, so one request's discover cannot
        # interleave with another's download; the owner thread then guarantees
        # every Playwright call inside it runs where the session was created.
        with self._lock:
            return self._run(work)

    def download_pdf_bytes(
        self, pdf_url: str, *, errors: list[str] | None = None
    ) -> bytes | None:
        """Download PDF bytes using the persistent browser session.

        *errors* is the sibling of `discover_pdf_url`'s: a session that dies
        mid-download must answer 503, not a 200 reporting no PDF.
        """
        from pzi.browser_pdf_hook import download_pdf as _download

        def work() -> bytes | None:
            session = self._ensure_session_on_owner_thread()
            return _download(
                pdf_url,
                browser=self._browser,
                _session=session,
                headless=self._headless,
                errors=errors,
            )

        with self._lock:
            return self._run(work)

    def close(self) -> None:
        """Close the browser session and its owner thread.  Idempotent."""
        with self._lock:
            executor, self._executor = self._executor, None
            if executor is None:
                # Nothing was ever launched through the owner thread — but a
                # caller (or a test) may still have installed a session
                # directly, and it is owed its `close`.
                self._close_on_owner_thread()
                return
            try:
                executor.submit(self._close_on_owner_thread).result()
            finally:
                executor.shutdown(wait=True)

    # -- internal ---------------------------------------------------------

    def _run(self, work: Any) -> Any:
        """Run *work* on the owner thread and return its result.

        Exceptions propagate to the caller unchanged, so a dead browser still
        reads as a failure rather than as an empty answer.  Never call this
        from the owner thread itself: the pool has exactly one worker, so a
        nested submit would wait for a thread that is already busy waiting.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="pzi-browser"
            )
        return self._executor.submit(work).result()

    def _ensure_session_on_owner_thread(self) -> Any:
        """The body of ``ensure_session``, already on the owner thread."""
        if self._session is not None:
            try:
                self._session._check_open()
                return self._session
            except RuntimeError:
                # session is closed / crashed — clean up and re-launch
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
        self._session = self._launch()
        return self._session

    def _close_on_owner_thread(self) -> None:
        """The body of ``close``, already on the owner thread."""
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def _launch(self) -> Any:
        """Launch a fresh BrowserSession (called on the owner thread)."""
        from pzi.browser_session import launch_browser

        return launch_browser(
            self._browser,
            self._profile_path,
            headless=self._headless,
        )
