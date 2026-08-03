"""Tests for the browser-availability probe and the env it depends on.

The browser suite has twice reported something other than what was true: once
by skipping real failures, once (v0.1.0b5's CI) by failing every browser test
because the hermetic ``$HOME`` fixture hid the downloaded browsers. Both are
harness bugs invisible to the browser tests themselves — nothing that needs a
browser can catch them — so they are pinned here, in the plain unit suite.
"""

import os
import sys
from pathlib import Path

import pytest

from tests.browser_probe import (
    BrowserStatus,
    _is_not_downloaded,
    browser_status,
    default_browsers_path,
    require_browser,
)


def test_browsers_path_survives_the_pinned_home():
    """The autouse ``_pin_home`` fixture must not hide the browser cache.

    ``$HOME`` is repointed at a tmpdir for every test in this suite, and
    Playwright resolves ``~/.cache/ms-playwright`` from it at launch time. If
    the pin lands without ``PLAYWRIGHT_BROWSERS_PATH`` set first, every browser
    test fails with "Executable doesn't exist at <tmpdir>/.cache/ms-playwright".
    """
    pinned = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    assert pinned, "PLAYWRIGHT_BROWSERS_PATH must be set before $HOME is pinned"
    assert Path(os.environ["HOME"]) not in Path(pinned).parents


def test_default_browsers_path_defers_to_an_explicit_setting(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/browsers")
    assert default_browsers_path() is None


def test_default_browsers_path_follows_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert default_browsers_path() == tmp_path / "cache" / "ms-playwright"


def test_default_browsers_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_browsers_path() == tmp_path / ".cache" / "ms-playwright"


def test_default_browsers_path_on_macos(monkeypatch, tmp_path):
    """The macOS half of the CI matrix runs this branch and nothing else does.

    Driving it through a patched ``sys.platform`` is what keeps it from being
    an assumption verified only by a runner we cannot reproduce locally — and
    ``XDG_CACHE_HOME`` must be ignored there, exactly as Playwright ignores it.
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "ignored"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_browsers_path() == tmp_path / "Library" / "Caches" / "ms-playwright"


def test_default_browsers_path_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert default_browsers_path() == tmp_path / "AppData" / "Local" / "ms-playwright"


def test_default_browsers_path_gives_up_on_an_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    assert default_browsers_path() is None


def test_never_downloaded_is_recognised_from_playwrights_own_wording():
    """``executable_path`` is no help — it returns a path either way.

    The message is the only signal that separates "no browsers here" from
    "browser is there and will not start", so it is pinned verbatim.
    """
    assert _is_not_downloaded(
        "BrowserType.launch: Executable doesn't exist at "
        "/home/u/.cache/ms-playwright/chromium_headless_shell-1234/"
        "chrome-headless-shell-linux64/chrome-headless-shell\n"
        "Please run the following command to download new browsers:\n"
        "    playwright install"
    )
    assert not _is_not_downloaded(
        "BrowserType.launch: Target page, context or browser has been closed"
    )
    assert not _is_not_downloaded(
        "BrowserType.launch: error while loading shared libraries: libnss3.so"
    )


def test_require_browser_fails_loudly_when_a_browser_is_broken(monkeypatch):
    """A browser that is present and will not start is a failure, not a skip."""
    monkeypatch.setattr(
        "tests.browser_probe.browser_status",
        lambda: BrowserStatus(usable=False, missing=False, reason="libnss3.so missing"),
    )
    with pytest.raises(AssertionError, match="libnss3.so missing"):
        require_browser()


def test_require_browser_skips_only_when_browsers_are_absent(monkeypatch):
    monkeypatch.setattr(
        "tests.browser_probe.browser_status",
        lambda: BrowserStatus(usable=False, missing=True, reason="not downloaded"),
    )
    with pytest.raises(pytest.skip.Exception, match="not downloaded"):
        require_browser()


def test_missing_browsers_are_a_failure_in_ci(monkeypatch):
    """In CI the download is a job step, so "not downloaded" means broken CI.

    Skipping there is how a workflow whose install step stopped running would
    report twenty green skips instead of a red job.
    """
    monkeypatch.setenv("CI", "true")
    browser_status.cache_clear()
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        _fake_playwright("Executable doesn't exist at /nowhere"),
    )
    try:
        status = browser_status()
    finally:
        browser_status.cache_clear()
    assert not status.usable
    assert not status.missing


def test_missing_browsers_are_a_skip_outside_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    browser_status.cache_clear()
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        _fake_playwright("Executable doesn't exist at /nowhere"),
    )
    try:
        status = browser_status()
    finally:
        browser_status.cache_clear()
    assert not status.usable
    assert status.missing
    assert "playwright install chromium" in status.reason


def _fake_playwright(launch_error: str):
    """A ``sync_playwright()`` stand-in whose browsers refuse to launch."""

    class _Launcher:
        executable_path = "/nowhere/chrome"

        def launch(self, **_kwargs):
            raise RuntimeError(f"BrowserType.launch: {launch_error}")

    class _Playwright:
        chromium = _Launcher()
        firefox = _Launcher()

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    return lambda: _Playwright()
