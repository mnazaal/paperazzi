"""Edge-case tests for HTML metadata extraction — covering previously untested branches."""

from pzi.html_metadata import (
    _from_citation_meta,
    _from_json_ld,
    _from_og,
    _merge,
    _parse_embedded_metadata,
    extract_metadata_from_html,
)

# ── _parse_embedded_metadata edges ──────────────────────────────────────

def test_parse_embedded_metadata_malformed_json_ld() -> None:
    """Line 30->exit: JSON-LD parsing raises exception, script is skipped."""
    html = """<html><head>
<script type="application/ld+json">{bad json</script>
<meta name="citation_title" content="Test Paper">
</head></html>"""
    meta, json_ld = _parse_embedded_metadata(html)
    assert json_ld == []
    assert meta.get("citation_title") == ["Test Paper"]


# ── _from_citation_meta edges ────────────────────────────────────────────

def test_from_citation_meta_empty_authors_filtered() -> None:
    """Lines 46-47: empty strings in citation_author are filtered out."""
    meta = {
        "citation_title": ["Test"],
        "citation_author": ["", "Real Author", ""],
    }
    record = _from_citation_meta(meta)
    assert record["authors"] == ["Real Author"]


# ── extract_metadata_from_html edges ─────────────────────────────────────

def test_extract_metadata_from_html_title_and_doi_both_missing() -> None:
    """Line 104: no title and no doi — returns None."""
    html = "<html><head></head><body>No metadata!</body></html>"
    result = extract_metadata_from_html(html)
    assert result is None


def test_extract_metadata_from_html_no_title_but_has_doi() -> None:
    """Line 107: no title but has doi — returns record (not None)."""
    html = """<html><head>
<meta name="citation_doi" content="10.1234/test.doi">
</head></html>"""
    result = extract_metadata_from_html(html)
    assert result is not None
    assert result["doi"] == "10.1234/test.doi"


# ── _from_json_ld edges ──────────────────────────────────────────────────

def test_from_json_ld_non_dict_item() -> None:
    """Lines 114->111: JSON-LD item is not a dict — skipped."""
    json_ld = ["not-a-dict", {"@type": "ScholarlyArticle", "name": "Paper"}]
    record = _from_json_ld(json_ld)
    assert record["title"] == "Paper"


def test_from_json_ld_not_scholarly_article() -> None:
    """Lines 116-117: JSON-LD item type not ScholarlyArticle — skipped."""
    json_ld = [{"@type": "WebPage", "name": "Not a paper"}]
    record = _from_json_ld(json_ld)
    assert record["title"] is None


# ── _from_og / _merge edges ────────────────────────────────────────────

def test_from_og_no_title() -> None:
    """_from_og returns None title when meta has no og:title."""
    record = _from_og({})
    assert record["title"] is None


def test_merge_preserves_existing_values() -> None:
    """_merge does not overwrite existing truthy values."""
    base = {"title": "Base", "authors": ["A"], "year": 2023, "venue": "V", "doi": "d"}
    extra = {"title": "Extra", "authors": [], "year": None, "venue": "V2", "doi": None}
    merged = _merge(base, extra)
    assert merged["title"] == "Base"
    assert merged["venue"] == "V"
    assert merged["doi"] == "d"
