"""Shared lazy Playwright-availability probe for browser-marked tests.

Launching a real Chromium/Firefox at *import time* would run the probe on
every test collection, even for a plain ``pytest -m "not browser"`` run that
never executes a browser test. Cache the check so the actual launch happens
at most once, lazily, on first use by an autouse skip-fixture.

The probe distinguishes **not installed** from **failed to launch**. Collapsing
the two is how a red browser suite stayed invisible: the previous version
returned a bare ``False`` for any exception, so a machine where Firefox merely
times out reported "20 skipped" while CI reported "10 failed" — and the skip
looked like the ordinary "no browsers here" case.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

#: Long enough for a cold start on a loaded CI runner, short enough that a
#: browser which will never come up does not eat the job's whole budget.
LAUNCH_TIMEOUT_MS = 60_000


@dataclass(frozen=True)
class BrowserStatus:
    """Whether browser tests can run here, and why not when they cannot."""

    usable: bool
    #: True only when the *binaries or package* are absent — the one case where
    #: skipping is honest. A launch that fails for any other reason is a
    #: failure, and the fixture raises rather than skipping.
    missing: bool
    reason: str


@functools.lru_cache(maxsize=1)
def browser_status() -> BrowserStatus:
    """Probe Playwright once, reporting *why* a browser is unusable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return BrowserStatus(
            usable=False,
            missing=True,
            reason=(
                "playwright is not installed. Install the optional extra: "
                "pip install 'paperazzi[playwright]'"
            ),
        )

    try:
        with sync_playwright() as p:
            for name in ("chromium", "firefox"):
                launcher = getattr(p, name)
                if launcher.executable_path is None:  # pragma: no cover — env-specific
                    return BrowserStatus(
                        usable=False,
                        missing=True,
                        reason=f"{name} binary not installed. Run: playwright install {name}",
                    )
                try:
                    launcher.launch(headless=True, timeout=LAUNCH_TIMEOUT_MS).close()
                except Exception as exc:
                    # Installed but broken: a missing system library, a sandbox
                    # restriction, a hang. Reporting this as "skipped" is what
                    # hid a red suite; the fixture turns it into a failure.
                    return BrowserStatus(
                        usable=False,
                        missing=False,
                        reason=f"{name} is installed but failed to launch: {exc}",
                    )
    except Exception as exc:  # pragma: no cover — driver-level failure
        return BrowserStatus(
            usable=False, missing=False, reason=f"playwright driver failed: {exc}"
        )
    return BrowserStatus(usable=True, missing=False, reason="")


def browser_available() -> bool:
    """Backwards-compatible boolean form of :func:`browser_status`."""
    return browser_status().usable


def require_browser() -> None:
    """Skip when browsers are absent; **fail** when they are present and broken.

    This is the distinction the old probe could not make. A machine with no
    browsers legitimately has nothing to run. A machine where a browser is
    installed and will not start has a problem that browser tests exist to
    surface — and reporting it as a skip is how ten real failures read as
    "20 skipped" on the developer's machine while CI showed them.
    """
    import pytest

    status = browser_status()
    if status.usable:
        return
    if status.missing:
        pytest.skip(reason=status.reason)
    raise AssertionError(f"browser tests cannot run: {status.reason}")


BROWSER_UNAVAILABLE_REASON = (
    "Playwright browser binaries not installed. Run: playwright install chromium firefox"
)
