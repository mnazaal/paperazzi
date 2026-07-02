"""Tests for browser_session_manager.py — BrowserSessionManager lifecycle."""

from unittest.mock import MagicMock

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
