"""Tests for browser_session.py — BrowserSession and lifecycle management."""

from unittest.mock import MagicMock

import pytest

from pzi.browser_session import (
    BrowserSession,
    FetchResult,
    browser_launch_options,
)


# === FetchResult ===


def test_fetch_result_pdf() -> None:
    r = FetchResult(status=200, content_type="application/pdf", body=b"%PDF-1.4 ok")
    assert r.is_pdf()

    r2 = FetchResult(status=200, content_type="text/html", body=b"<html>")
    assert not r2.is_pdf()

    r3 = FetchResult(status=404, content_type="application/pdf", body=b"%PDF-")
    assert not r3.is_pdf()

    r4 = FetchResult(status=200, content_type="application/pdf", body=b"not pdf")
    assert not r4.is_pdf()


# === browser_launch_options ===


def test_launch_options_chromium() -> None:
    opts = browser_launch_options("chromium")
    assert opts == {"headless": True}


def test_launch_options_firefox() -> None:
    opts = browser_launch_options("firefox")
    assert opts["headless"] is True
    assert opts["firefox_user_prefs"]["pdfjs.disabled"] is True


# === BrowserSession basic operations ===


def test_session_navigate_and_url() -> None:
    class FakePage:
        url = "https://example.com/article"
        def goto(self, url, **kw):
            return "response"

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    resp = s.navigate("https://example.com")
    assert resp == "response"
    assert s.current_url() == "https://example.com/article"


def test_session_evaluate() -> None:
    class FakePage:
        def evaluate(self, js):
            return ["result1", "result2"]

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.evaluate("some js")
    assert result == ["result1", "result2"]


def test_session_fetch_direct_pdf() -> None:
    class FakeResponse:
        status = 200
        headers = {"content-type": "application/pdf"}
        def body(self):
            return b"%PDF-1.4 direct"

    class FakeRequest:
        def get(self, url):
            return FakeResponse()

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.fetch_direct("https://example.com/paper.pdf")
    assert result.status == 200
    assert result.is_pdf()
    assert result.body == b"%PDF-1.4 direct"


def test_session_fetch_direct_exception() -> None:
    class FakeRequest:
        def get(self, url):
            raise RuntimeError("network failure")

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.fetch_direct("https://example.com/paper.pdf")
    assert result.status == -1
    assert not result.is_pdf()


def test_session_click_first() -> None:
    class FakeLocatorFirst:
        def click(self, timeout=None):
            pass

    class FakeLocator:
        first = FakeLocatorFirst()

    class FakePage:
        def locator(self, sel):
            return FakeLocator()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    assert s.click_first("button:has-text('Accept')") is True


def test_session_click_first_fails() -> None:
    class FakePage:
        def locator(self, sel):
            raise RuntimeError("not found")

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    assert s.click_first("button:has-text('Nonexistent')") is False


def test_session_try_click_first() -> None:
    events = []

    class FakePage:
        def locator(self, sel):
            events.append(sel)
            if len(events) < 3:
                raise RuntimeError("nope")
            return MagicMock()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    ok = s.try_click_first(["sel1", "sel2", "sel3"])
    assert ok is True
    assert len(events) == 3


def test_session_try_click_first_all_fail() -> None:
    events = []
    class FakePage:
        def locator(self, sel):
            events.append(sel)
            raise RuntimeError("all fail")
    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    ok = s.try_click_first(["sel1", "sel2"])
    assert ok is False
    assert len(events) == 2
    events = []

    class FakePage:
        def locator(self, sel):
            events.append(sel)
            if len(events) < 3:
                raise RuntimeError("nope")
            m = MagicMock()
            return m

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    ok = s.try_click_first(["sel1", "sel2", "sel3"])
    assert ok is True
    assert len(events) == 3


def test_session_wait_network_idle() -> None:
    class FakePage:
        def wait_for_load_state(self, state, timeout=None):
            pass

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    s.wait_network_idle()
    # Should not raise


# === BrowserSession lifecycle ===


def test_session_close_idempotent() -> None:
    events = []

    class FakePlaywright:
        def stop(self):
            events.append("stop")

    class FakeContext:
        def close(self):
            events.append("ctx_close")

    class FakeBrowser:
        def close(self):
            events.append("browser_close")

    class FakePage:
        pass

    s = BrowserSession(
        playwright=FakePlaywright(),
        browser_ref=(FakeBrowser(), FakeContext()),
        page=FakePage(),
    )
    s.close()
    s.close()  # idempotent
    assert events == ["ctx_close", "browser_close", "stop"]


def test_session_close_persistent() -> None:
    events = []

    class FakePlaywright:
        def stop(self):
            events.append("stop")

    class FakePersistent:
        def close(self):
            events.append("persist_close")

    s = BrowserSession(
        playwright=FakePlaywright(),
        browser_ref=FakePersistent(),
        page=object(),
    )
    s.close()
    assert events == ["persist_close", "stop"]


def test_session_close_after_closed() -> None:
    s = BrowserSession(playwright=object(), browser_ref=object(), page=object())
    s.close()
    with pytest.raises(RuntimeError, match="closed"):
        s.navigate("https://example.com")


# === open_browser_session context manager ===


@pytest.mark.skip(reason="needs full Playwright mock for launch")
def test_open_browser_session_context(monkeypatch) -> None:
    pass
