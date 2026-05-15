"""Cover _launch_browser and open_browser_session in browser_session.py by mocking Playwright."""

import sys
import types

from pzi.browser_session import BrowserSession, _launch_browser, open_browser_session

# --- _launch_browser with mocked Playwright ---


def create_fake_playwright_modules():
    """Create fake playwright modules that return fake browser objects."""

    class FakePage:
        url = "https://test.com"

        def goto(self, url, **kw):
            return type("R", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"})()

        def evaluate(self, js):
            return []

        def new_page(self):
            return FakePage()

    class FakeContext:
        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeBrowser:
        def new_context(self):
            return FakeContext()

        def close(self):
            pass

        @staticmethod
        def launch_persistent_context(**kw):
            return FakeContext()

        @staticmethod
        def launch(**kw):
            return FakeBrowser()

    class FakePlaywright:
        firefox = FakeBrowser
        chromium = FakeBrowser

        def stop(self):
            pass

    class FakeSync:
        @staticmethod
        def start():
            return FakePlaywright()

    fake_sync = types.ModuleType("playwright.sync_api")
    setattr(fake_sync, "sync_playwright", FakeSync)

    fake_pw = types.ModuleType("playwright")
    return fake_pw, fake_sync


# --- Tests ---


def test_launch_browser_chromium_no_profile(monkeypatch):
    fake_pw, fake_sync = create_fake_playwright_modules()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    session = _launch_browser("chromium", None)
    assert isinstance(session, BrowserSession)
    assert not session._closed
    session.close()


def test_launch_browser_firefox_no_profile(monkeypatch):
    fake_pw, fake_sync = create_fake_playwright_modules()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    session = _launch_browser("firefox", None)
    assert isinstance(session, BrowserSession)
    session.close()


def test_launch_browser_with_profile_chromium(monkeypatch, tmp_path):
    fake_pw, fake_sync = create_fake_playwright_modules()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    profile = tmp_path / "chrome-profile"
    profile.mkdir()
    session = _launch_browser("chromium", str(profile))
    assert isinstance(session, BrowserSession)
    session.close()


def test_launch_browser_with_profile_firefox(monkeypatch, tmp_path):
    fake_pw, fake_sync = create_fake_playwright_modules()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    session = _launch_browser("firefox", str(profile))
    assert isinstance(session, BrowserSession)
    session.close()


def test_open_browser_session_context_manager(monkeypatch):
    fake_pw, fake_sync = create_fake_playwright_modules()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)

    with open_browser_session(browser="chromium") as session:
        assert isinstance(session, BrowserSession)
        assert not session._closed
        url = session.current_url()
        assert url is not None
    # After context exit, session should be closed
    assert session._closed


def test_wait_network_idle_import_error(monkeypatch):
    """Cover the ImportError branch in wait_network_idle."""
    fake_pw, fake_sync = create_fake_playwright_modules()
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync)
    session = _launch_browser("chromium", None)
    del sys.modules["playwright.sync_api"]
    session.wait_network_idle()
    session.close()
