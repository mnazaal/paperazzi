"""Shared lazy Playwright-availability probe for browser-marked tests.

Launching a real Chromium/Firefox at *import time* would run the probe on
every test collection, even for a plain ``pytest -m "not browser"`` run that
never executes a browser test. Cache the check so the actual launch happens
at most once, lazily, on first use by an autouse skip-fixture.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def browser_available() -> bool:
    """Return True if Playwright is installed and Chromium/Firefox can launch."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
            p.firefox.launch(headless=True).close()
        return True
    except Exception:
        return False


BROWSER_UNAVAILABLE_REASON = (
    "Playwright browser binaries not installed. Run: playwright install chromium firefox"
)
