"""Edge-case tests for Semantic Scholar client — covering previously untested branches."""

import json

from pzi.metadata_sources import (
    _s2_normalize_paper as _normalize_paper,
    fetch_semantic_scholar_record,
)

# ── fetch_semantic_scholar_record edges ──────────────────────────────────

def test_fetch_semantic_scholar_record_error_response() -> None:
    """Line 46: response contains 'error' key — returns None."""
    result = fetch_semantic_scholar_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"error": "not found"}),
    )
    assert result is None


def test_fetch_semantic_scholar_record_message_response() -> None:
    """Line 46: response contains 'message' key — returns None."""
    result = fetch_semantic_scholar_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"message": "no paper"}),
    )
    assert result is None


# ── _normalize_paper edges ───────────────────────────────────────────────

def test_normalize_paper_author_not_dict() -> None:
    """Lines 48->44: raw author entry is not a dict — skipped."""
    paper = {
        "paperId": "abc",
        "title": "Test Paper",
        "authors": ["not-a-dict", {"name": "Valid Author"}],
        "year": 2023,
    }
    record = _normalize_paper(paper)
    assert record["authors"] == ["Valid Author"]


def test_normalize_paper_author_name_empty() -> None:
    """Lines 57->62: author name is empty string — not appended."""
    paper = {
        "paperId": "abc",
        "title": "Test Paper",
        "authors": [
            {"name": ""},
            {"name": None},
            {"name": "Real Author"},
        ],
        "year": 2023,
    }
    record = _normalize_paper(paper)
    assert record["authors"] == ["Real Author"]


def test_normalize_paper_location_not_dict() -> None:
    """Lines 59->62: externalIds is not a dict — doi stays None."""
    paper = {
        "paperId": "abc",
        "title": "Test Paper",
        "authors": [],
        "year": 2023,
        "externalIds": "not-a-dict",
    }
    record = _normalize_paper(paper)
    assert record["doi"] is None


def test_normalize_paper_open_access_not_dict() -> None:
    """Lines 66->69: openAccessPdf is not a dict — pdf_url stays None."""
    paper = {
        "paperId": "abc",
        "title": "Test Paper",
        "authors": [],
        "year": 2023,
        "openAccessPdf": "not-a-dict",
    }
    record = _normalize_paper(paper)
    assert "pdf_url" not in record


def test_normalize_paper_open_access_pdf_url_not_string() -> None:
    """Line 82: openAccessPdf.url is not a string — pdf_url stays None."""
    paper = {
        "paperId": "abc",
        "title": "Test Paper",
        "authors": [],
        "year": 2023,
        "openAccessPdf": {"url": 12345},
    }
    record = _normalize_paper(paper)
    assert "pdf_url" not in record
