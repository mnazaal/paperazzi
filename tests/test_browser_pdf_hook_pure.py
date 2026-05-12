"""Tests for browser_pdf_hook using FakeBrowserSession (no Playwright needed)."""

from pzi import browser_pdf_hook as hook
from pzi.browser_session import FetchResult
from tests.fake_session import FakeBrowserSession, make_pdf_response


# === Pure helper: _is_pdf_url ===

def test_is_pdf_url() -> None:
    assert hook._is_pdf_url("https://example.com/paper.pdf")
    assert hook._is_pdf_url("https://example.com/paper.PDF")
    assert not hook._is_pdf_url("https://example.com/paper.html")


# === JS builder constants ===

def test_discovery_js_string() -> None:
    assert "querySelectorAll" in hook.DISCOVERY_JS


# === discover_pdf_url with FakeBrowserSession ===

def test_discover_no_candidates() -> None:
    s = FakeBrowserSession(evaluate_results=[[]])
    result = hook.discover_pdf_url("https://example.com", _session=s)
    assert result is None


def test_discover_candidates_found() -> None:
    s = FakeBrowserSession(evaluate_results=[["/paper.pdf"]])
    result = hook.discover_pdf_url(
        "https://example.com",
        _session=s,
        _resolve=lambda page_url, c: ["https://example.com/paper.pdf"],
    )
    assert result == "https://example.com/paper.pdf"


def test_discover_post_click_pdf() -> None:
    s = FakeBrowserSession(
        url="https://journal.test/download.pdf",
        evaluate_results=[[]],
    )
    result = hook.discover_pdf_url(
        "https://example.com",
        _session=s,
        _click=lambda page: True,
        _resolve=lambda url, c: [],
    )
    assert result == "https://journal.test/download.pdf"


# === download_pdf with FakeBrowserSession ===

def test_download_direct_pdf() -> None:
    s = FakeBrowserSession(
        fetch_result=(200, "application/pdf", b"%PDF-1.4 test"),
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 test"


def test_download_direct_non_pdf() -> None:
    s = FakeBrowserSession(
        fetch_result=(200, "text/html", b"<html></html>"),
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result is None


def test_download_goto_pdf() -> None:
    s = FakeBrowserSession(
        fetch_result=(-1, None, b""),
        goto_results=[make_pdf_response()],
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 test"


def test_download_candidate_link_found() -> None:
    html = make_pdf_response(body=b"%PDF-1.4 linked")  # not used directly
    s = FakeBrowserSession(
        fetch_result=(-1, None, b""),
        goto_results=[
            type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"}),
            type("F", (), {"headers": {"content-type": "application/pdf"}, "body": lambda: b"%PDF-1.4 linked"}),
        ],
        evaluate_results=[["https://journal.test/linked.pdf"]],
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 linked"


def test_download_candidate_non_string_skipped() -> None:
    html = type("F", (), {"headers": {"content-type": "text/html"}, "body": lambda: b"<html>"})
    s = FakeBrowserSession(
        fetch_result=(-1, None, b""),
        goto_results=[html],
        evaluate_results=[[None, 123, "https://journal.test/paper.pdf"]],
    )
    # second goto for the valid candidate
    s._gotos.append(
        type("F", (), {"headers": {"content-type": "application/pdf"}, "body": lambda: b"%PDF-1.4 found"})
    )
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result == b"%PDF-1.4 found"


def test_download_outer_exception() -> None:
    s = FakeBrowserSession()

    def failing_navigate(url, **kw):
        raise RuntimeError("bang")

    s.navigate = failing_navigate
    s.fetch_direct = lambda url: FetchResult(status=-1, content_type=None, body=b"")
    result = hook.download_pdf("https://example.com/paper.pdf", _session=s)
    assert result is None
