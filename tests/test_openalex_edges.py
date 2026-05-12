"""Edge-case tests for OpenAlex client — covering previously untested branches."""

import json

from pzi.openalex import _normalize_work, fetch_openalex_record

# ── fetch_openalex_record edges ──────────────────────────────────────────

def test_fetch_openalex_record_no_id_in_response() -> None:
    """Line 38: response has no 'id' key — returns None."""
    result = fetch_openalex_record(
        "10.1234/foo",
        fetch_text=lambda _: json.dumps({"not-id": "something"}),
    )
    assert result is None


# ── normalize_work edges ─────────────────────────────────────────────────

def test_normalize_work_author_not_dict_and_authorship_not_dict() -> None:
    """Lines 40->36: authorship is not a dict (skipped)."""
    work = {
        "id": "W123",
        "title": "Test Paper",
        "authorships": ["not-a-dict", {"author": {"display_name": "Valid Author"}}],
        "publication_year": 2023,
        "doi": "https://doi.org/10.1234/test",
    }
    record = _normalize_work(work)
    assert record["authors"] == ["Valid Author"]


def test_normalize_work_author_dict_missing_display_name() -> None:
    """Lines 42->36: author is dict but display_name is missing."""
    work = {
        "id": "W123",
        "title": "Test Paper",
        "authorships": [
            {"author": {"not_display_name": "whoops"}},
            {"author": {"display_name": ""}},  # empty string
            {"author": {"display_name": "Good Author"}},
        ],
        "publication_year": 2023,
        "doi": "https://doi.org/10.1234/test",
    }
    record = _normalize_work(work)
    assert record["authors"] == ["Good Author"]


def test_normalize_work_primary_location_not_dict() -> None:
    """Lines 51->54: primary_location is not a dict."""
    work = {
        "id": "W123",
        "title": "Test Paper",
        "authorships": [],
        "publication_year": 2023,
        "doi": "https://doi.org/10.1234/test",
        "primary_location": "not-a-dict",
    }
    record = _normalize_work(work)
    assert record["venue"] is None


def test_normalize_work_source_not_dict_in_primary_location() -> None:
    """Lines 56->60: source within primary_location is not a dict."""
    work = {
        "id": "W123",
        "title": "Test Paper",
        "authorships": [],
        "publication_year": 2023,
        "doi": "https://doi.org/10.1234/test",
        "primary_location": {"source": "not-a-dict"},
    }
    record = _normalize_work(work)
    assert record["venue"] is None


def test_normalize_work_open_access_not_dict() -> None:
    """Lines 64->67: open_access is not a dict — pdf_url stays None."""
    work = {
        "id": "W123",
        "title": "Test Paper",
        "authorships": [],
        "publication_year": 2023,
        "doi": "https://doi.org/10.1234/test",
        "open_access": "not-a-dict",
    }
    record = _normalize_work(work)
    assert "pdf_url" not in record
