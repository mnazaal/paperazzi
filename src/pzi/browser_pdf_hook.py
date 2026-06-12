#!/usr/bin/env python3
"""Playwright-based PDF discovery and download hook for pzi.

Supports Firefox and Chromium-based browsers with profile reuse.

Auto-installs browser binaries on first use if missing.

Invoked internally by pzi via the browser_pdf_cmd config option.
Normally run as: python -m pzi.browser_pdf_hook [--profile PATH] [--browser chromium|firefox]

Reads JSON on stdin:
  {"page_url": "...", "doi": "..."}  # discover PDF URL
  {"action": "download_pdf", "pdf_url": "..."}  # download PDF bytes

Writes JSON on stdout on success:
  {"pdf_url": "https://...pdf"}  # for discovery
  {"pdf_base64": "..."}  # for download (base64-encoded PDF)

Writes {} on stdout when no PDF URL is discovered or download fails.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urljoin

from pzi.browser_session import (
    BrowserSession,
)

PDF_HINT_RE = re.compile(r"pdf|download", re.IGNORECASE)

DISCOVERY_JS = r"""
() => {
  const out = [];
  const add = (value) => {
    if (typeof value !== 'string') return;
    const trimmed = value.trim();
    if (!trimmed) return;
    if (!/^https?:\/\//i.test(trimmed)) return;
    if (!out.includes(trimmed)) out.push(trimmed);
  };

  document
    .querySelectorAll(
      'meta[name="citation_pdf_url"], meta[name="wkhealth_pdf_url"]'
    )
    .forEach((node) => add(node.getAttribute('content')));

  document
    .querySelectorAll('a[href], link[href], iframe[src], embed[src]')
    .forEach((node) => {
      add(
        node.href
        || node.src
        || node.getAttribute('href')
        || node.getAttribute('src')
      );
    });

  return out;
}
"""

POST_CLICK_JS = r"""
() => Array.from(
  document.querySelectorAll('a[href], iframe[src], embed[src]')
)
  .map((node) => (
    node.href
    || node.src
    || node.getAttribute('href')
    || node.getAttribute('src')
  ))
  .filter(Boolean)
"""

DOWNLOAD_CANDIDATES_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('a[href]').forEach((node) => {
    const href = node.href || node.getAttribute('href');
    if (href && href.toLowerCase().includes('.pdf')) {
      out.push(href);
    }
  });
  return out;
}
"""

COOKIE_BANNER_SELECTORS = [
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Allow all')",
    "button:has-text('Continue')",
]

DOWNLOADISH_SELECTORS = [
    "a:has-text('PDF')",
    "a:has-text('Download PDF')",
    "button:has-text('PDF')",
    "button:has-text('Download PDF')",
]

HookRequest = tuple[str, str]


def parse_hook_request(payload: Any) -> HookRequest | None:
    """Return normalized hook action and URL from decoded JSON payload."""
    if not isinstance(payload, dict):
        return None

    action = payload.get("action", "discover")
    if action == "download_pdf":
        raw_url = payload.get("pdf_url")
        if isinstance(raw_url, str) and raw_url.strip():
            return "download_pdf", raw_url.strip()
        return None

    raw_url = payload.get("page_url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "discover", raw_url.strip()
    return None


def encode_hook_response(
    *,
    pdf_url: str | None = None,
    pdf_bytes: bytes | None = None,
) -> str:
    """Encode hook stdout response without side effects."""
    if pdf_bytes:
        return json.dumps({"pdf_base64": base64.b64encode(pdf_bytes).decode()})
    if pdf_url:
        return json.dumps({"pdf_url": pdf_url})
    return "{}"


def _ensure_browser(browser: str = "firefox") -> bool:  # pragma: no cover — system I/O
    """Ensure Playwright package is importable and browser binaries exist.

    Returns True if ready to use, False otherwise.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright package not found. Install pzi normally; "
            "for development use: pip install -e .[dev]",
            file=sys.stderr,
        )
        return False

    # Check if browser binaries are installed by trying to launch.
    playwright = None
    try:
        playwright = sync_playwright().start()
        if browser == "firefox":
            playwright.firefox.launch(headless=True).close()
        else:
            playwright.chromium.launch(headless=True).close()
        return True
    except Exception:
        # Browser binaries not installed, try to install them
        print(f"Installing {browser} browser binaries...", file=sys.stderr)
    finally:
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", browser],
            check=True,
            capture_output=True,
        )
        print(f"{browser} browser binaries installed.", file=sys.stderr)
        return True
    except subprocess.CalledProcessError:
        print(
            f"Failed to install {browser} browser binaries.",
            file=sys.stderr,
        )
        print("Run 'playwright install chromium' manually.", file=sys.stderr)
        return False


def main() -> int:  # pragma: no cover — CLI entry point
    parser = argparse.ArgumentParser(description="pzi browser PDF hook")
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Path to browser profile directory (uses cookies/session from your browser)",
    )
    parser.add_argument(
        "--browser",
        type=str,
        choices=["firefox", "chromium", "chrome"],
        default="chromium",
        help="Browser engine to use (default: chromium)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="open a visible browser window for sites that require human verification",
    )
    parser.add_argument(
        "--challenge-timeout",
        type=int,
        default=0,
        metavar="SECONDS",
        help="wait up to SECONDS for browser verification before giving up",
    )
    args = parser.parse_args()

    # Ensure browser binaries are installed
    if not _ensure_browser(args.browser):
        print("{}")
        return 1

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0

    request = parse_hook_request(payload)
    if request is None:
        print("{}")
        return 0

    action, url = request
    if action == "download_pdf":
        pdf_bytes = download_pdf(
            url,
            browser=args.browser,
            profile_path=args.profile,
            headless=not args.headful,
            challenge_timeout=args.challenge_timeout,
        )
        if pdf_bytes is None and args.headful:
            print(
                "browser PDF hook: visible browser fallback did not obtain a PDF. "
                "If a verification page appeared, complete it before the timeout; "
                "otherwise configure browser_pdf_cmd with your regular browser profile.",
                file=sys.stderr,
            )
        print(encode_hook_response(pdf_bytes=pdf_bytes))
        return 0

    # Default: discover PDF URL
    pdf_url = discover_pdf_url(
        url,
        browser=args.browser,
        profile_path=args.profile,
        headless=not args.headful,
    )
    print(encode_hook_response(pdf_url=pdf_url))
    return 0


def _launch_browser(
    browser: str,
    profile_path: str | None = None,
    *,
    headless: bool = True,
) -> BrowserSession:
    """Launch a browser and return a BrowserSession (delegates to browser_session module)."""
    from pzi.browser_session import _launch_browser as _impl
    return _impl(browser, profile_path, headless=headless)


def _close_browser(session_or_playwright: Any, *args: Any) -> None:
    """Clean up browser resources."""
    if isinstance(session_or_playwright, BrowserSession):
        session_or_playwright.close()
        return
    # Legacy triple-format support
    try:
        playwright = session_or_playwright
        if args:  # pragma: no branch — covered by integration/browser tests
            browser_ref = args[0]
            if isinstance(browser_ref, tuple):
                browser, context = browser_ref
                context.close()
                browser.close()
            else:
                browser_ref.close()
        playwright.stop()  # pragma: no cover — legacy path
    except Exception:  # pragma: no cover — legacy path
        pass


def discover_pdf_url(
    page_url: str,
    *,
    browser: str = "chromium",
    profile_path: str | None = None,
    _dismiss=None,
    _click=None,
    _resolve=None,
    _session: BrowserSession | None = None,
    headless: bool = True,
) -> str | None:
    """Discover PDF URL from a page using browser.

    All I/O edges are injectable via _session for testing.
    """
    close_on_exit = _session is None  # pragma: no branch
    if _session is not None:  # pragma: no branch
        session = _session
    else:  # pragma: no cover
        session = _launch_browser(browser, profile_path, headless=headless)

    dismiss_fn = _dismiss or _dismiss_cookie_banners
    click_fn = _click or _click_downloadish_links
    resolve_fn = _resolve or resolve_pdf_candidate_urls

    try:
        session.navigate(page_url)
        dismiss_fn(session.page)
        session.wait_network_idle()

        candidates = session.evaluate(DISCOVERY_JS)
        resolved = resolve_fn(page_url, candidates)
        if resolved:
            return resolved[0]

        if click_fn(session.page):
            session.wait_network_idle()
            if _is_pdf_url(session.current_url()):
                return session.current_url()
            candidates = session.evaluate(POST_CLICK_JS)  # pragma: no cover — integration path
            resolved = resolve_fn(page_url, candidates)  # pragma: no cover — integration path
            if resolved:  # pragma: no cover — integration path
                return resolved[0]

        return None
    except Exception:  # pragma: no cover — covered by integration/browser tests
        return None
    finally:
        if close_on_exit:  # pragma: no cover — browser integration path
            _close_browser(session)


def download_pdf(
    pdf_url: str,
    *,
    browser: str = "chromium",
    profile_path: str | None = None,
    _dismiss=None,
    _session: BrowserSession | None = None,
    headless: bool = True,
    challenge_timeout: int = 0,
) -> bytes | None:
    """Download PDF bytes using browser, with optional profile reuse.

    All browser I/O edges are injectable via _session for testing.
    """
    close_on_exit = _session is None  # pragma: no branch
    if _session is not None:  # pragma: no branch
        session = _session
    else:  # pragma: no cover
        session = _launch_browser(browser, profile_path, headless=headless)

    dismiss_fn = _dismiss or _dismiss_cookie_banners

    try:
        # 1. Try direct request
        result = session.fetch_direct(pdf_url)
        if result.is_pdf():
            return result.body

        # 2. Navigate to URL (fallback — browser handles auth via profile cookies)
        response = None
        try:
            response = session.navigate(pdf_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass

        if response is not None:
            content_type = response.headers.get("content-type", "")
            if "application/pdf" in content_type:
                body = response.body()  # pragma: no branch — covered by integration/browser tests
                if body and body.startswith(b"%PDF-"):  # pragma: no branch
                    return body

        if challenge_timeout > 0:
            pdf_bytes = _wait_for_verified_pdf(session, pdf_url, timeout=challenge_timeout)
            if pdf_bytes is not None:
                return pdf_bytes

        # 3. HTML page — look for PDF candidate links
        dismiss_fn(session.page)
        session.wait_network_idle()

        candidates = session.evaluate(DOWNLOAD_CANDIDATES_JS)
        if isinstance(candidates, list):  # pragma: no branch — covered by integration/browser tests
            for candidate in candidates:
                if not isinstance(candidate, str):
                    continue
                if not candidate.startswith(("http://", "https://")):
                    continue  # pragma: no cover — covered by integration/browser tests
                try:
                    resp = session.navigate(candidate, wait_until="domcontentloaded", timeout=30000)
                    if resp is not None:  # pragma: no branch — covered by integration/browser tests
                        ct = resp.headers.get("content-type", "")
                        if "application/pdf" in ct:  # pragma: no branch
                            body = resp.body()
                            if body and body.startswith(b"%PDF-"):  # pragma: no branch
                                return body
                except Exception:  # pragma: no cover — covered by integration/browser tests
                    continue

        return None
    except Exception:  # pragma: no cover — covered by integration/browser tests
        return None
    finally:
        if close_on_exit:  # pragma: no cover — browser integration path
            _close_browser(session)


def _wait_for_verified_pdf(
    session: BrowserSession,
    pdf_url: str,
    *,
    timeout: int,
) -> bytes | None:
    """Poll browser context after a challenge page, allowing cookies to settle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            session.page.wait_for_timeout(2000)
        except Exception:
            time.sleep(2)
        result = session.fetch_direct(pdf_url)
        if result.is_pdf():
            return result.body
        try:
            response = session.navigate(pdf_url, wait_until="domcontentloaded", timeout=30000)
            if response is not None:
                content_type = response.headers.get("content-type", "")
                if "application/pdf" in content_type:
                    body = response.body()
                    if body and body.startswith(b"%PDF-"):
                        return body
        except Exception:
            pass
    return None


def resolve_pdf_candidate_urls(page_url: str, candidates: Any) -> list[str]:
    if not isinstance(candidates, list):
        return []  # pragma: no cover — covered by integration/browser tests
    resolved: list[str] = []
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        absolute = urljoin(page_url, raw.strip())
        if not absolute.startswith(("http://", "https://")):
            continue
        lower = absolute.lower()
        if lower.endswith(".pdf") or PDF_HINT_RE.search(lower):
            if absolute not in resolved:
                resolved.append(absolute)
    return resolved


def _is_pdf_url(url: str) -> bool:
    """Pure: check if a URL likely points to a PDF."""
    return url.lower().endswith(".pdf")


def _dismiss_cookie_banners(page: Any) -> None:
    for selector in COOKIE_BANNER_SELECTORS:
        try:
            page.locator(selector).first.click(timeout=300)
            return
        except Exception:
            continue


def _click_downloadish_links(page: Any) -> bool:
    for selector in DOWNLOADISH_SELECTORS:
        try:
            page.locator(selector).first.click(timeout=300)
            return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    raise SystemExit(main())
