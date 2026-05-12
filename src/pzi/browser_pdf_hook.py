#!/usr/bin/env python3
"""Playwright-based PDF discovery and download hook for pzi.

Supports Firefox and Chromium-based browsers with profile reuse.

Auto-installs browser binaries on first use if missing.

Usage:
  # Discover PDF URL from a page
  echo '{"page_url": "https://..."}' | browser_pdf_hook.py

  # Download PDF bytes using browser profile
  echo '{"action": "download_pdf", "pdf_url": "https://..."}' \
    | browser_pdf_hook.py --profile /path/to/profile

  # Download PDF with specific browser type
  echo '{"action": "download_pdf", "pdf_url": "https://..."}' \
    | browser_pdf_hook.py --profile /path/to/profile --browser firefox

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
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

PDF_HINT_RE = re.compile(r"pdf|download", re.IGNORECASE)

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


def browser_launch_options(browser: str) -> dict[str, Any]:
    """Return Playwright launch kwargs for browser name."""
    options: dict[str, Any] = {"headless": True}
    if browser == "firefox":
        options["firefox_user_prefs"] = {
            "browser.download.folderList": 2,
            "browser.download.manager.showWhenStarting": False,
            "pdfjs.disabled": True,
        }
    return options


def _ensure_browser(browser: str = "firefox") -> bool:
    """Ensure Playwright package is importable and browser binaries exist.

    Returns True if ready to use, False otherwise.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright package not found. Install with: pip install playwright",
            file=sys.stderr,
        )
        return False

    # Check if browser binaries are installed by trying to launch
    try:
        playwright = sync_playwright().start()
        if browser == "firefox":
            playwright.firefox.launch(headless=True).close()
        else:
            playwright.chromium.launch(headless=True).close()
        playwright.stop()
        return True
    except Exception:
        # Browser binaries not installed, try to install them
        print(f"Installing {browser} browser binaries...", file=sys.stderr)
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


def main() -> int:
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
        )
        print(encode_hook_response(pdf_bytes=pdf_bytes))
        return 0

    # Default: discover PDF URL
    pdf_url = discover_pdf_url(
        url,
        browser=args.browser,
        profile_path=args.profile,
    )
    print(encode_hook_response(pdf_url=pdf_url))
    return 0


def _launch_browser(browser: str, profile_path: str | None = None) -> tuple[Any, Any, Any]:
    """Launch browser with optional profile reuse."""
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()

    launch_kwargs = browser_launch_options(browser)

    if profile_path:
        profile = Path(profile_path).expanduser()
        if browser == "firefox":
            # Firefox uses user_data_dir for profile
            persistent_context = playwright.firefox.launch_persistent_context(
                user_data_dir=str(profile),
                **launch_kwargs,
            )
            page = persistent_context.new_page()
            return playwright, persistent_context, page
        else:
            # Chromium/Chrome use user_data_dir
            persistent_context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                **launch_kwargs,
            )
            page = persistent_context.new_page()
            return playwright, persistent_context, page

    # No profile - launch headless without persistent context
    if browser == "firefox":
        browser_instance = playwright.firefox.launch(**launch_kwargs)
        context = browser_instance.new_context()
        page = context.new_page()
        return playwright, (browser_instance, context), page
    else:
        browser_instance = playwright.chromium.launch(**launch_kwargs)
        context = browser_instance.new_context()
        page = context.new_page()
        return playwright, (browser_instance, context), page


def _close_browser(playwright: Any, browser_ref: Any, page: Any) -> None:
    """Clean up browser resources."""
    try:
        if isinstance(browser_ref, tuple):
            # Headless mode: (browser, context)
            browser, context = browser_ref
            context.close()
            browser.close()
        else:
            # Persistent context
            browser_ref.close()
        playwright.stop()
    except Exception:
        pass


def discover_pdf_url(
    page_url: str,
    *,
    browser: str = "chromium",
    profile_path: str | None = None,
) -> str | None:
    """Discover PDF URL from a page using browser."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return None

    playwright = None
    browser_ref = None
    page = None

    try:
        playwright, browser_ref, page = _launch_browser(browser, profile_path)
        page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
        _dismiss_cookie_banners(page)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass

        candidates = page.evaluate(
            r"""
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
        )
        resolved = resolve_pdf_candidate_urls(page_url, candidates)
        if resolved:
            _close_browser(playwright, browser_ref, page)
            return resolved[0]

        clicked = _click_downloadish_links(page)
        if clicked:
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass
            post_click_url = page.url
            if post_click_url.lower().endswith(".pdf"):
                _close_browser(playwright, browser_ref, page)
                return post_click_url
            candidates = page.evaluate(
                r"""
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
            )
            resolved = resolve_pdf_candidate_urls(page_url, candidates)
            if resolved:
                _close_browser(playwright, browser_ref, page)
                return resolved[0]

        _close_browser(playwright, browser_ref, page)
    except Exception:
        if playwright and browser_ref and page:
            _close_browser(playwright, browser_ref, page)
        return None
    return None


def download_pdf(
    pdf_url: str,
    *,
    browser: str = "chromium",
    profile_path: str | None = None,
) -> bytes | None:
    """Download PDF bytes using browser, with optional profile reuse for authenticated access."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return None

    playwright = None
    browser_ref = None
    page = None

    try:
        playwright, browser_ref, page = _launch_browser(browser, profile_path)

        # Try direct request first (avoids download-trigger issues in Chromium)
        try:
            req_response = page.request.get(pdf_url)
            if req_response.status == 200:
                ct = req_response.headers.get("content-type", "")
                if "application/pdf" in ct:
                    pdf_bytes = req_response.body()
                    if pdf_bytes.startswith(b"%PDF-"):
                        _close_browser(playwright, browser_ref, page)
                        return pdf_bytes
        except Exception:
            pass

        # Fallback: navigate to URL - browser handles auth via profile cookies
        try:
            response = page.goto(
                pdf_url, wait_until="domcontentloaded", timeout=60000
            )
        except Exception:
            response = None

        # Check if we got a PDF response
        if response:
            content_type = response.headers.get("content-type", "")
            if "application/pdf" in content_type:
                pdf_bytes = response.body()
                if pdf_bytes.startswith(b"%PDF-"):
                    _close_browser(playwright, browser_ref, page)
                    return pdf_bytes

        # If not a direct PDF, try to find PDF link on page
        _dismiss_cookie_banners(page)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass

        # Look for PDF links
        candidates = page.evaluate(
            r"""
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
        )

        if isinstance(candidates, list) and candidates:
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                    try:
                        pdf_response = page.goto(
                            candidate,
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                        if pdf_response:
                            ct = pdf_response.headers.get("content-type", "")
                            if "application/pdf" in ct:
                                pdf_bytes = pdf_response.body()
                                if pdf_bytes.startswith(b"%PDF-"):
                                    _close_browser(playwright, browser_ref, page)
                                    return pdf_bytes
                    except Exception:
                        continue

        _close_browser(playwright, browser_ref, page)
    except Exception:
        if playwright and browser_ref and page:
            _close_browser(playwright, browser_ref, page)
        return None
    return None


def resolve_pdf_candidate_urls(page_url: str, candidates: Any) -> list[str]:
    if not isinstance(candidates, list):
        return []
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


def _dismiss_cookie_banners(page: Any) -> None:
    selectors = [
        "button:has-text('Accept')",
        "button:has-text('I agree')",
        "button:has-text('Allow all')",
        "button:has-text('Continue')",
    ]
    for selector in selectors:
        try:
            page.locator(selector).first.click(timeout=1000)
            return
        except Exception:
            continue


def _click_downloadish_links(page: Any) -> bool:
    selectors = [
        "a:has-text('PDF')",
        "a:has-text('Download PDF')",
        "button:has-text('PDF')",
        "button:has-text('Download PDF')",
    ]
    for selector in selectors:
        try:
            page.locator(selector).first.click(timeout=1000)
            return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    raise SystemExit(main())
