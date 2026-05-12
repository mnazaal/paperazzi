"""Playwright integration tests for browser_pdf_hook.

Requires Playwright browser binaries: ``playwright install chromium firefox``
"""

from __future__ import annotations

import functools
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

_BROWSER_AVAILABLE: bool = False

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        p.chromium.launch(headless=True).close()
        p.firefox.launch(headless=True).close()
    _BROWSER_AVAILABLE = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not _BROWSER_AVAILABLE,
    reason="Playwright browser binaries not installed. Run: playwright install chromium firefox",
)


@pytest.fixture
def test_server(tmp_path: Path):
    """Yield a local HTTP server base URL for the duration of a test."""
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        addr = server.server_address
        host_str = str(addr[0])
        port_int = int(addr[1])
        yield f"http://{host_str}:{port_int}"
    finally:
        server.shutdown()


@pytest.mark.parametrize("browser", ["chromium", "firefox"])
def test_discover_pdf_url_from_meta_tag(
    browser: str, test_server: str, tmp_path: Path
) -> None:
    """Discover PDF URL from citation_pdf_url meta tag."""
    from pzi.browser_pdf_hook import discover_pdf_url

    pdf_url = f"{test_server}/paper.pdf"
    html = f"""<!DOCTYPE html>
<html>
<head><meta name="citation_pdf_url" content="{pdf_url}"></head>
<body>Article</body>
</html>"""
    (tmp_path / "article.html").write_text(html)
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4\ntest")

    result = discover_pdf_url(f"{test_server}/article.html", browser=browser)
    assert result == pdf_url


@pytest.mark.parametrize("browser", ["chromium", "firefox"])
def test_discover_pdf_url_from_link(
    browser: str, test_server: str, tmp_path: Path
) -> None:
    """Discover PDF URL from an <a> link with .pdf href."""
    from pzi.browser_pdf_hook import discover_pdf_url

    html = """<!DOCTYPE html>
<html>
<body><a href="paper.pdf">Download PDF</a></body>
</html>"""
    (tmp_path / "page.html").write_text(html)
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4\ntest")

    result = discover_pdf_url(f"{test_server}/page.html", browser=browser)
    assert result == f"{test_server}/paper.pdf"


@pytest.mark.parametrize("browser", ["chromium", "firefox"])
def test_discover_pdf_url_returns_none_when_no_pdf(
    browser: str, test_server: str, tmp_path: Path
) -> None:
    """Return None when page has no PDF links."""
    from pzi.browser_pdf_hook import discover_pdf_url

    html = "<!DOCTYPE html><html><body>No PDF here</body></html>"
    (tmp_path / "nopdf.html").write_text(html)

    result = discover_pdf_url(f"{test_server}/nopdf.html", browser=browser)
    assert result is None


@pytest.mark.parametrize("browser", ["chromium", "firefox"])
def test_download_pdf_bytes(browser: str, test_server: str, tmp_path: Path) -> None:
    """Download PDF bytes directly via browser."""
    from pzi.browser_pdf_hook import download_pdf

    (tmp_path / "document.pdf").write_bytes(b"%PDF-1.4\ndownloaded content")

    result = download_pdf(f"{test_server}/document.pdf", browser=browser)
    assert result is not None
    assert result == b"%PDF-1.4\ndownloaded content"


@pytest.mark.parametrize("browser", ["chromium", "firefox"])
def test_download_pdf_returns_none_for_non_pdf(
    browser: str, test_server: str, tmp_path: Path
) -> None:
    """Return None when URL serves HTML instead of PDF."""
    from pzi.browser_pdf_hook import download_pdf

    html = "<!DOCTYPE html><html><body>Not a PDF</body></html>"
    (tmp_path / "notpdf.html").write_text(html)

    result = download_pdf(f"{test_server}/notpdf.html", browser=browser)
    assert result is None
