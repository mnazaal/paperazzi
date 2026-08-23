"""Fake BrowserSession for unit tests — zero Playwright dependencies."""


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
        self.page = self  # acts as its own page

    def navigate(self, url, *, wait_until="domcontentloaded", timeout=30000):
        if self._gotos and self._goto_idx < len(self._gotos):
            resp = self._gotos[self._goto_idx]
            self._goto_idx += 1
            return resp
        return None

    def current_url(self):
        return self._url

    def evaluate(self, js):
        if self._evaluate:
            return self._evaluate.pop(0) if isinstance(self._evaluate[0], list) else self._evaluate
        return []

    def fetch_direct(self, url):
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
        pass

    def wait_for_timeout(self, milliseconds):
        pass

    def close(self):
        self._closed = True

    def _check_open(self):
        """Raise like the real session does once the browser is gone."""
        if self._closed:
            raise RuntimeError("browser session is closed")

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
