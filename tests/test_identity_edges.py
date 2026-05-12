"""Edge tests for identity.py uncovered lines (line 65: _deduplicate_identities)."""

from pzi.identity import (
    _deduplicate_identities,
    build_identity_index,
    extract_identities,
    find_exact_match,
)


def test_deduplicate_identities_no_duplicates() -> None:
    """No duplicates → same list."""
    ids = [
        {"kind": "doi", "value": "10.1/foo"},
        {"kind": "arxiv", "value": "2401.12345"},
        {"kind": "url", "value": "https://example.com"},
    ]
    result = _deduplicate_identities(ids)
    assert result == ids


def test_deduplicate_identities_with_duplicates() -> None:
    """Duplicate identities are removed (first kept)."""
    ids = [
        {"kind": "doi", "value": "10.1/foo"},
        {"kind": "doi", "value": "10.1/foo"},
        {"kind": "arxiv", "value": "2401.12345"},
        {"kind": "arxiv", "value": "2401.12345"},
    ]
    result = _deduplicate_identities(ids)
    assert len(result) == 2
    assert result[0] == {"kind": "doi", "value": "10.1/foo"}
    assert result[1] == {"kind": "arxiv", "value": "2401.12345"}


def test_deduplicate_identities_empty() -> None:
    assert _deduplicate_identities([]) == []


def test_extract_identities_no_ids() -> None:
    """Record with no identities → empty list."""
    assert extract_identities({}) == []


def test_extract_identities_whitespace_only() -> None:
    """Whitespace values are treated as not valid."""
    result = extract_identities({"doi": "  ", "arxiv_id": "", "canonical_url": "\t"})
    assert result == []


def test_extract_identities_all_present() -> None:
    record = {
        "doi": "10.1/foo",
        "arxiv_id": "2401.12345",
        "canonical_url": "https://example.com/paper",
    }
    result = extract_identities(record)
    assert len(result) == 3


def test_build_identity_index_multiple_positions() -> None:
    """Same identity maps to all positions."""
    records = [
        {"doi": "10.1/foo", "title": "A"},
        {"doi": "10.1/foo", "title": "B"},
    ]
    idx = build_identity_index(records)
    assert idx[("doi", "10.1/foo")] == [0, 1]


def test_find_exact_match_found() -> None:
    existing = [
        {"doi": "10.1/existing"},
        {"arxiv_id": "2401.55555"},
    ]
    record = {"doi": "10.1/existing"}
    assert find_exact_match(record, existing) == 0


def test_find_exact_match_not_found() -> None:
    existing: list[dict] = [{"title": "Something"}]
    assert find_exact_match({"doi": "10.1/new"}, existing) is None


def test_find_exact_match_empty_existing() -> None:
    assert find_exact_match({"doi": "10.1/foo"}, []) is None
