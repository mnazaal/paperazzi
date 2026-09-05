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
import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: Long enough for a cold start on a loaded CI runner, short enough that a
#: browser which will never come up does not eat the job's whole budget.
LAUNCH_TIMEOUT_MS = 60_000


def default_browsers_path() -> Path | None:
    """Where Playwright looks for downloaded browsers, given the current env.

    Mirrors the driver's own ``registryDirectory`` resolution: an explicit
    ``PLAYWRIGHT_BROWSERS_PATH`` wins, otherwise it is the platform cache
    directory plus ``ms-playwright``. Returns ``None`` when the caller has
    already pinned the path (nothing to compute) or when the platform is one
    Playwright does not support.

    This exists because the browser cache is resolved from ``$HOME``, and the
    unit suite repoints ``$HOME`` at a throwaway directory. Without pinning the
    path *before* that, every browser test looks for Chromium under an empty
    tmpdir and fails as "installed but failed to launch".
    """
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return None
    if sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        cache = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform.startswith("linux"):
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    else:
        # Playwright itself refuses to run here, so there is no path to give.
        return None
    return cache / "ms-playwright"


#: Playwright's own wording when a browser was never downloaded. Matching the
#: message is the only way to tell "never installed" from "installed but
#: broken": ``BrowserType.executable_path`` returns a path whether or not
#: anything is there, and for headless Chromium the binary that must exist is
#: not even the one it names (``chrome-headless-shell`` is a separate download).
_NOT_DOWNLOADED_MARKERS = (
    "executable doesn't exist",
    "please run the following command to download new browsers",
)


def _is_not_downloaded(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _NOT_DOWNLOADED_MARKERS)


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
                "pip install 'pzi[playwright]'"
            ),
        )

    try:
        with sync_playwright() as p:
            for name in ("chromium", "firefox"):
                launcher = getattr(p, name)
                try:
                    launcher.launch(headless=True, timeout=LAUNCH_TIMEOUT_MS).close()
                except Exception as exc:
                    if _is_not_downloaded(str(exc)) and not os.environ.get("CI"):
                        # Nothing was ever downloaded here — the legitimate
                        # "no browsers on this machine" case. In CI the
                        # download is a job step, so the same message means a
                        # broken workflow and must stay a failure.
                        return BrowserStatus(
                            usable=False,
                            missing=True,
                            reason=f"{name} binaries not downloaded. Run: playwright install {name}",
                        )
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
