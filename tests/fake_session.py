"""Fake BrowserSession for unit tests — zero Playwright dependencies.

The fake mirrors two constraints of the real session, because a fake that
mirrors neither is a fake that certifies nothing:

* **Thread affinity.** Playwright's sync API binds its objects to the thread
  that created them, so driving a session from a second thread raises rather
  than blocking. The fake raises the same way. Without this, a manager that
  launches on one request thread and is used from another passes every unit
  test and fails against a real browser on the second request.
* **Argument shape.** The fake used to discard the JS and the URL it was
  handed and pop a queued result regardless, so a hook that sent nothing, or
  sent a malformed URL, looked identical to one that worked.

Both checks are opt-out (`thread_bound=False`, `strict=False`) for the few
tests that deliberately drive the session from elsewhere.
"""

import threading


class SyncApiThreadError(Exception):
    """Raised when the fake session is driven from a foreign thread.

    Deliberately **not** a ``RuntimeError``. Playwright's sync API fails a
    cross-thread call with ``greenlet.error``, which derives from ``Exception``,
    so a manager whose crash-tolerance catches ``RuntimeError`` does not catch
    it — the request fails rather than quietly relaunching. Modelling it as a
    ``RuntimeError`` would hand that manager a free pass and hide exactly the
    defect this fake exists to expose.
    """


class FakeBrowserSession:
    """Mimics BrowserSession without Playwright."""

    def __init__(
        self,
        *,
        url: str = "https://journal.test/article",
        evaluate_results: list | None = None,
        fetch_result: tuple | None = None,
        fetch_results: list | None = None,
        click_results: list | None = None,
        goto_results: list | None = None,
        thread_bound: bool = True,
        strict: bool = True,
    ):
        self._url = url
        self._evaluate = evaluate_results or []
        self._fetch = fetch_result  # (status, content_type, body)
        # Consumed in order, for flows that fetch more than once: `download_pdf`
        # tries the URL directly, then each candidate link it finds.
        self._fetches = list(fetch_results or [])
        self._clicks = click_results or [False]
        self._gotos = goto_results or []
        self._goto_idx = 0
        self._closed = False
        self._thread_bound = thread_bound
        self._strict = strict
        self._owner_thread = threading.get_ident()
        #: Every call the session was asked to make, in order, as
        #: ``(method, argument)``. Lets a test assert what the hook sent
        #: rather than only what it got back.
        self.calls: list[tuple[str, object]] = []
        self.page = self  # acts as its own page

    def navigate(self, url, *, wait_until="domcontentloaded", timeout=30000):
        self._check_open()
        self._check_thread()
        self._check_url(url, "navigate")
        self.calls.append(("navigate", url))
        if self._gotos and self._goto_idx < len(self._gotos):
            resp = self._gotos[self._goto_idx]
            self._goto_idx += 1
            return resp
        return None

    def current_url(self):
        self._check_open()
        self._check_thread()
        return self._url

    def evaluate(self, js):
        self._check_open()
        self._check_thread()
        self._check_js(js)
        self.calls.append(("evaluate", js))
        # Always consume one entry, whether it is a scalar result or a list
        # result — the previous version only popped when the *first* queued
        # item was itself a list, so a test queuing two different scalar
        # results (`evaluate_results=[True, False]`) got the whole remaining
        # queue back unconsumed on every call and silently saw `True` twice.
        if self._evaluate:
            return self._evaluate.pop(0)
        return []

    def fetch_direct(self, url):
        self._check_open()
        self._check_thread()
        self._check_url(url, "fetch_direct")
        self.calls.append(("fetch_direct", url))
        if self._fetches:
            from pzi.browser_session import FetchResult
            status, content_type, body = self._fetches.pop(0)
            return FetchResult(status=status, content_type=content_type, body=body)
        if self._fetch:
            from pzi.browser_session import FetchResult
            return FetchResult(
                status=self._fetch[0],
                content_type=self._fetch[1],
                body=self._fetch[2],
            )
        from pzi.browser_session import FetchResult
        return FetchResult(status=-1, content_type=None, body=b"")

    def wait_network_idle(self, *, timeout=5000):
        self._check_thread()

    def wait_for_timeout(self, milliseconds):
        self._check_thread()

    def close(self):
        # Deliberately not thread-checked: the real session is closed from
        # whichever thread owns the manager, and a close that raised would
        # turn cleanup into a second failure.
        self._closed = True

    def _check_open(self):
        """Raise like the real session does once the browser is gone.

        Thread-checked, because the real ``_check_open`` asks Playwright
        whether the browser is still connected — a sync-API call like any
        other, and the one the manager uses to decide a session is reusable.
        """
        self._check_thread()
        if self._closed:
            raise RuntimeError("browser session is closed")

    def _adopt_current_thread(self):
        """Rebind ownership to the calling thread.

        The real session is constructed by ``launch_browser`` on whichever
        thread called it, so its owner is decided at launch.  A fake built
        ahead of time in a test body would otherwise be owned by the test
        thread and reject the manager's own worker.
        """
        self._owner_thread = threading.get_ident()

    def _check_thread(self):
        """Raise like Playwright's sync API does across threads.

        The real failure is a greenlet error from deep inside Playwright; what
        matters for a test is that it *raises* rather than quietly working, so
        the message names the constraint instead of imitating the traceback.
        """
        if not self._thread_bound:
            return
        if threading.get_ident() != self._owner_thread:
            raise SyncApiThreadError(
                "Playwright sync API called from a different thread than the "
                "one that created the session"
            )

    def _check_url(self, url, method):
        if not self._strict:
            return
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise AssertionError(f"{method}() got a non-URL: {url!r}")

    def _check_js(self, js):
        if not self._strict:
            return
        if not isinstance(js, str) or not js.strip():
            raise AssertionError(f"evaluate() got no JavaScript: {js!r}")

    # Legacy triple-format support
    def locator(self, sel):
        return type("L", (), {
            "first": type("F", (), {
                "click": lambda t=None: (_ for _ in ()).throw(RuntimeError("nope"))
            })
        })()

    def goto(self, url, **kw):
        return self.navigate(url, **kw)


def make_fake_response(content_type="text/html", body=b"<html></html>", status=200):
    """Create a fake Playwright response object."""
    class FakeResponse:
        headers = {"content-type": content_type}
        pass
    FakeResponse.status = status
    FakeResponse.body = lambda self=None: body
    return FakeResponse()


def make_pdf_response(body=b"%PDF-1.4 test", status=200):
    """Create a fake PDF response."""
    return make_fake_response(content_type="application/pdf", body=body, status=status)
