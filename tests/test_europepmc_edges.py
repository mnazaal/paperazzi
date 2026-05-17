"""Edge-case tests for Europe PMC client — covering previously untested branches."""

import json

from pzi.metadata_sources import _extract_pdf_url, fetch_europepmc_pdf_url

# ── fetch_europepmc_pdf_url edges ────────────────────────────────────────

def test_fetch_europepmc_pdf_url_empty_results() -> None:
    """Line 41: resultList.result is empty — returns None."""
    result = fetch_europepmc_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"resultList": {"result": []}}),
    )
    assert result is None


def test_fetch_europepmc_pdf_url_http_exception() -> None:
    """Line 45: Exception during fetch, returns None."""
    result = fetch_europepmc_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: (_ for _ in ()).throw(ValueError("bad json")),
    )
    assert result is None


def test_fetch_europepmc_pdf_url_missing_result_list() -> None:
    """Line 50: response missing resultList key entirely."""
    result = fetch_europepmc_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"wrong": "structure"}),
    )
    assert result is None


# ── _extract_pdf_url edges ───────────────────────────────────────────────

def test_extract_pdf_url_full_text_url_list_not_dict() -> None:
    """Line 68: fullTextUrlList is not a dict."""
    result = _extract_pdf_url({"fullTextUrlList": "not-a-dict"})
    assert result is None


def test_extract_pdf_url_full_text_url_not_list() -> None:
    """Line 77: fullTextUrl is not a list."""
    result = _extract_pdf_url(
        {"fullTextUrlList": {"fullTextUrl": "not-a-list"}}
    )
    assert result is None
