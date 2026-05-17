"""Edge-case tests for Crossref client — covering previously untested branches."""

import json

from pzi.metadata_sources import (
    _crossref_extract_pdf_url as _extract_pdf_url,
    _crossref_normalize_work as _normalize_work,
    fetch_crossref_pdf_url,
)

# ── _normalize_work edges ────────────────────────────────────────────────

def test_normalize_work_family_no_given() -> None:
    """Line 61-62: author with family but no given name."""
    work = {
        "title": ["Paper One"],
        "author": [{"family": "Smith"}],
        "DOI": "10.1234/test.1",
    }
    record = _normalize_work(work)
    assert record["authors"] == ["Smith"]


def test_normalize_work_author_not_dict_skipped() -> None:
    """Line 56: continue when raw_author is not a dict."""
    work = {
        "title": ["Paper One"],
        "author": ["not-a-dict", {"family": "Jones", "given": "A"}],
        "DOI": "10.1234/test.2",
    }
    record = _normalize_work(work)
    assert record["authors"] == ["Jones, A"]


def test_normalize_work_year_candidate_not_int() -> None:
    """Line 75: candidate is not an int, so year stays None."""
    work = {
        "title": ["Paper One"],
        "author": [],
        "DOI": "10.1234/test.3",
        "published-online": {"date-parts": [["2023"]]},  # string, not int
    }
    record = _normalize_work(work)
    assert record["year"] is None


def test_normalize_work_empty_container_title() -> None:
    """Line 80: container-title is empty list — venue stays None."""
    work = {
        "title": ["Paper One"],
        "author": [],
        "DOI": "10.1234/test.4",
        "container-title": [],
    }
    record = _normalize_work(work)
    assert record["venue"] is None


def test_normalize_work_raw_doi_falsy() -> None:
    """Line 83: raw_doi is None (falsy), doi stays None."""
    work = {
        "title": ["Paper One"],
        "author": [],
    }
    record = _normalize_work(work)
    assert record["doi"] is None


# ── _extract_pdf_url edges ───────────────────────────────────────────────

def test_extract_pdf_url_link_not_dict_first_pass() -> None:
    """Line 109: first pass skips non-dict link entries."""
    work = {
        "link": [
            "not-a-dict",
            {"URL": "https://x.com/paper.pdf", "content-type": "application/pdf"},
        ]
    }
    result = _extract_pdf_url(work)
    assert result == "https://x.com/paper.pdf"


def test_extract_pdf_url_content_type_pdf_but_url_not_string() -> None:
    """Line 113: PDF content-type found but URL is not a string."""
    work = {
        "link": [
            {"URL": 123, "content-type": "application/pdf"},
            {"URL": "https://x.com/paper.pdf", "content-type": "text/html"},
        ]
    }
    result = _extract_pdf_url(work)
    # Falls through first pass, second pass catches .pdf extension
    assert result == "https://x.com/paper.pdf"


def test_extract_pdf_url_url_empty_string() -> None:
    """Line 113: URL is empty string, first pass skips."""
    work = {
        "link": [
            {"URL": "", "content-type": "application/pdf"},
        ]
    }
    result = _extract_pdf_url(work)
    assert result is None


def test_extract_pdf_url_link_not_dict_second_pass() -> None:
    """Line 119: second pass skips non-dict entries."""
    work = {
        "link": [
            "not-a-dict",
            {"URL": "https://x.com/paper.pdf"},
        ]
    }
    result = _extract_pdf_url(work)
    assert result == "https://x.com/paper.pdf"


def test_extract_pdf_url_second_pass_url_whitespace() -> None:
    """Line 121: URL is whitespace string, second pass skips."""
    work = {
        "link": [
            {"URL": "   "},
        ]
    }
    result = _extract_pdf_url(work)
    assert result is None


# ── fetch_crossref_pdf_url edges ─────────────────────────────────────────

def test_fetch_crossref_pdf_url_work_not_dict() -> None:
    """Line 42: Crossref response message is not a dict."""
    result = fetch_crossref_pdf_url(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"message": "not-a-dict"}),
    )
    assert result is None
