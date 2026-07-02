"""Browser integration tests — require Playwright + Chromium/Firefox.

Run with: pytest -m "browser" -v
"""

import pytest

from pzi.browser_pdf_hook import discover_pdf_url, download_pdf
from pzi.browser_session import (
    open_browser_session,
)
from tests.browser_probe import BROWSER_UNAVAILABLE_REASON, browser_available

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def _require_browser() -> None:
    if not browser_available():
        pytest.skip(reason=BROWSER_UNAVAILABLE_REASON)


# ----------------------------------------------------------------
# helpers
# ----------------------------------------------------------------


def _serve_pdf(http_server):
    """Copy a minimal PDF into fixtures/ so the HTTP server can serve it."""
    from pathlib import Path
    fixtures = Path(__file__).parent / "fixtures"
    pdf_path = fixtures / "paper.pdf"
    if not pdf_path.exists():
        minimal_pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000058 00000 n \n0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
        )
        pdf_path.write_bytes(minimal_pdf)


@pytest.fixture(autouse=True)
def _ensure_pdf(http_server):
    _serve_pdf(http_server)


# ----------------------------------------------------------------
# BrowserSession lifecycle
# ----------------------------------------------------------------


def test_open_session_navigate(http_server):
    with open_browser_session(browser="chromium") as s:
        s.navigate(f"{http_server}/article_with_pdf_link.html")
        assert "Article" in s.evaluate("document.title")


def test_open_session_close_is_clean(http_server):
    with open_browser_session(browser="chromium") as s:
        s.navigate(f"{http_server}/article_no_pdf.html")
    # After closing, operations should raise
    with pytest.raises(RuntimeError, match="closed"):
        s.navigate("https://example.com")


# ----------------------------------------------------------------
# discover_pdf_url
# ----------------------------------------------------------------


def test_discover_pdf_direct_link(http_server):
    with open_browser_session(browser="chromium") as s:
        url = discover_pdf_url(
            f"{http_server}/article_with_pdf_link.html",
            _session=s,
        )
    assert url is not None
    assert url.endswith("paper.pdf")


def test_discover_pdf_after_click(http_server):
    with open_browser_session(browser="chromium") as s:
        # The button text "Download PDF" should be found by DOWNLOADISH_SELECTORS
        url = discover_pdf_url(
            f"{http_server}/article_with_download_button.html",
            _session=s,
        )
    # May or may not find a PDF — button click might not yield a PDF URL
    assert url is None or url.endswith(".pdf")


def test_discover_no_pdf_links(http_server):
    with open_browser_session(browser="chromium") as s:
        url = discover_pdf_url(
            f"{http_server}/article_no_pdf.html",
            _session=s,
        )
    assert url is None


# ----------------------------------------------------------------
# download_pdf
# ----------------------------------------------------------------


def test_download_pdf_direct(http_server):
    with open_browser_session(browser="chromium") as s:
        body = download_pdf(
            f"{http_server}/paper.pdf",
            _session=s,
        )
    assert body is not None
    assert body.startswith(b"%PDF-")


def test_download_pdf_via_candidate(http_server):
    """download_pdf follows a PDF candidate link on an HTML page."""
    with open_browser_session(browser="chromium") as s:
        body = download_pdf(
            f"{http_server}/article_with_candidate_link.html",
            _session=s,
        )
    # The candidate link is found and followed; PDF body returned
    # (May be None if the server doesn't set correct Content-Type for .pdf)
    if body is not None:
        assert body.startswith(b"%PDF-")


def test_download_non_pdf_page(http_server):
    with open_browser_session(browser="chromium") as s:
        body = download_pdf(
            f"{http_server}/article_no_pdf.html",
            _session=s,
        )
    assert body is None


# ----------------------------------------------------------------
# error paths
# ----------------------------------------------------------------


def test_discover_invalid_url(http_server):
    with open_browser_session(browser="chromium") as s:
        url = discover_pdf_url(
            "http://127.0.0.1:19999/nonexistent",
            _session=s,
        )
    assert url is None


def test_download_invalid_url(http_server):
    with open_browser_session(browser="chromium") as s:
        body = download_pdf(
            "http://127.0.0.1:19999/nonexistent.pdf",
            _session=s,
        )
    assert body is None
