"""Tests for browser_session_manager.py — BrowserSessionManager lifecycle."""

from unittest.mock import MagicMock

import pytest
from fake_session import FakeBrowserSession

from pzi.browser_session_manager import BrowserSessionManager


def test_close_is_idempotent_when_no_session_launched() -> None:
    manager = BrowserSessionManager()

    manager.close()
    manager.close()


def test_close_only_closes_underlying_session_once() -> None:
    """Regression check for P4.3: close() must be safe to call twice (e.g. on
    idle shutdown racing an explicit close) without double-closing the
    underlying Playwright session."""
    manager = BrowserSessionManager()
    fake_session = MagicMock()
    manager._session = fake_session

    manager.close()
    manager.close()

    fake_session.close.assert_called_once()
    assert manager._session is None


def _install_fake_launcher(monkeypatch, sessions: list) -> list:
    """Make ``_launch`` hand out ``sessions`` in order, recording each launch."""
    import pzi.browser_session

    launched: list = []
    queue = list(sessions)

    def fake_launch(browser, profile_path, *, headless=True, url_allowed=None):
        session = queue.pop(0)
        launched.append((browser, profile_path, headless, session))
        return session

    monkeypatch.setattr(pzi.browser_session, "launch_browser", fake_launch)
    return launched


def test_ensure_session_launches_once_and_reuses_the_live_session(monkeypatch) -> None:
    first = FakeBrowserSession()
    launched = _install_fake_launcher(monkeypatch, [first, FakeBrowserSession()])
    manager = BrowserSessionManager(browser="firefox", profile_path="/p", headless=False)

    assert manager.ensure_session() is first
    assert manager.ensure_session() is first
    assert launched == [("firefox", "/p", False, first)]


def test_ensure_session_relaunches_after_the_browser_crashes(monkeypatch) -> None:
    """The documented crash-tolerance: a dead session is replaced, not reused.

    ``_check_open`` raising ``RuntimeError`` is how the real ``BrowserSession``
    reports that Playwright is gone.
    """
    crashed = FakeBrowserSession()
    replacement = FakeBrowserSession()
    launched = _install_fake_launcher(monkeypatch, [crashed, replacement])
    manager = BrowserSessionManager()

    assert manager.ensure_session() is crashed
    crashed.close()  # the browser died under us

    assert manager.ensure_session() is replacement
    assert len(launched) == 2
    assert crashed._closed is True


@pytest.mark.parametrize(
    ("method", "hook_name", "argument"),
    [
        ("discover_pdf_url", "discover_pdf_url", "https://journal.test/article"),
        ("download_pdf_bytes", "download_pdf", "https://journal.test/paper.pdf"),
    ],
)
def test_each_delegate_passes_the_persistent_session_and_its_browser_settings(
    monkeypatch, method: str, hook_name: str, argument: str
) -> None:
    import pzi.browser_pdf_hook

    session = FakeBrowserSession()
    _install_fake_launcher(monkeypatch, [session])
    manager = BrowserSessionManager(browser="firefox", profile_path="/p", headless=False)

    calls: list = []

    def record(url, **kwargs):
        calls.append((url, kwargs))
        return "sentinel"

    monkeypatch.setattr(pzi.browser_pdf_hook, hook_name, record)

    assert getattr(manager, method)(argument) == "sentinel"
    assert calls == [
        (
            argument,
            {
                "browser": "firefox",
                "_session": session,
                "headless": False,
                # Forwarded even when the caller passed none, so the hook's own
                # signature stays one shape. See the `errors` test below.
                "errors": None,
            },
        )
    ]


@pytest.mark.parametrize(
    ("method", "hook_name", "argument"),
    [
        ("discover_pdf_url", "discover_pdf_url", "https://journal.test/article"),
        ("download_pdf_bytes", "download_pdf", "https://journal.test/paper.pdf"),
    ],
)
def test_each_delegate_forwards_the_errors_list_to_the_hook(
    monkeypatch, method: str, hook_name: str, argument: str
) -> None:
    """A crashed session must reach the caller, not read as "no PDF" (G1).

    `_handle_browser_discover_post` passes `errors=` only when the manager's
    method accepts it (`accepts_keyword`). That guard degrades silently, so
    when these two methods lacked the parameter the whole fix no-opped on the
    persistent-session path while working on the subprocess one — and no test
    said so. This pins the forwarding itself: the list the caller owns is the
    list the hook appends to.
    """
    import pzi.browser_pdf_hook

    session = FakeBrowserSession()
    _install_fake_launcher(monkeypatch, [session])
    manager = BrowserSessionManager(browser="firefox", profile_path="/p", headless=False)

    def record(url, **kwargs):
        kwargs["errors"].append("browser session crashed")
        return None

    monkeypatch.setattr(pzi.browser_pdf_hook, hook_name, record)

    errors: list[str] = []
    assert getattr(manager, method)(argument, errors=errors) is None
    assert errors == ["browser session crashed"]


@pytest.mark.parametrize(
    ("method", "hook_name", "argument"),
    [
        ("discover_pdf_url", "discover_pdf_url", "https://journal.test/article"),
        ("download_pdf_bytes", "download_pdf", "https://journal.test/paper.pdf"),
    ],
)
def test_the_lock_is_held_for_the_whole_delegate_call(
    monkeypatch, method: str, hook_name: str, argument: str
) -> None:
    """Playwright's sync page cannot be driven from two threads at once.

    Holding the lock only across ``ensure_session`` would let a second request
    thread enter the delegate while the first is still inside it, so the check
    has to happen from *another* thread while the delegate is running.
    """
    import threading

    import pzi.browser_pdf_hook

    _install_fake_launcher(monkeypatch, [FakeBrowserSession()])
    manager = BrowserSessionManager()

    acquired_by_other_thread: list[bool] = []

    def record(url, **kwargs):
        def try_acquire() -> None:
            got = manager._lock.acquire(blocking=False)
            acquired_by_other_thread.append(got)
            if got:
                manager._lock.release()

        other = threading.Thread(target=try_acquire)
        other.start()
        other.join()
        return None

    monkeypatch.setattr(pzi.browser_pdf_hook, hook_name, record)

    getattr(manager, method)(argument)

    assert acquired_by_other_thread == [False]
