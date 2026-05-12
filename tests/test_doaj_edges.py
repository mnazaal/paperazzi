"""Edge-case tests for DOAJ client — covering previously untested branches."""

import json

from pzi.doaj import _extract_pdf_url, fetch_doaj_pdf_url

# ── fetch_doaj_pdf_url edges ─────────────────────────────────────────────

def test_fetch_doaj_pdf_url_empty_results() -> None:
    """Line 37: results list is empty — returns None."""
    result = fetch_doaj_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"results": []}),
    )
    assert result is None


def test_fetch_doaj_pdf_url_http_exception() -> None:
    """Line 41: Exception during fetch, returns None."""
    result = fetch_doaj_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: (_ for _ in ()).throw(OSError("network")),
    )
    assert result is None


def test_fetch_doaj_pdf_url_no_results_key() -> None:
    """Line 45: response missing 'results' key — fallback returns None."""
    result = fetch_doaj_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"not_results": []}),
    )
    assert result is None


# ── _extract_pdf_url edges ───────────────────────────────────────────────

def test_extract_pdf_url_no_bibjson() -> None:
    """Line 60: bibjson is not a dict."""
    article = {"bibjson": "not-a-dict"}
    result = _extract_pdf_url(article)
    assert result is None


def test_extract_pdf_url_links_not_list() -> None:
    """Line 63: link field is not a list."""
    article = {"bibjson": {"link": "not-a-list"}}
    result = _extract_pdf_url(article)
    assert result is None
