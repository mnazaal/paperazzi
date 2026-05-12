"""Edge tests for bibtex.py uncovered lines (extract_note_field, _parse_note_text)."""

from pzi.bibtex import (
    _build_note,
    _parse_note_text,
    bibtex_entry_to_record,
    extract_note_field,
    record_to_bibtex_entry,
)

# ── extract_note_field ───────────────────────────────────────────

def test_extract_note_field_none_input() -> None:
    """Cover line where _empty_to_none returns None for note=None."""
    assert extract_note_field(None, "PDF") is None
    assert extract_note_field(None, "Abstract") is None


def test_extract_note_field_empty_string() -> None:
    """_empty_to_none returns None for whitespace-only note."""
    assert extract_note_field("   ", "PDF") is None
    assert extract_note_field("", "Abstract") is None


def test_extract_note_field_label_not_found() -> None:
    """Label not present in note → return None."""
    assert extract_note_field("Some note | Other: info", "PDF") is None


def test_extract_note_field_label_found_with_value() -> None:
    """Label found with non-empty value after prefix."""
    assert extract_note_field("PDF: http://example.com/a.pdf | Other: info", "PDF") == "http://example.com/a.pdf"


def test_extract_note_field_label_found_empty_after_prefix() -> None:
    """Label found but value is only whitespace → return None (or empty → None)."""
    assert extract_note_field("PDF:   ", "PDF") is None
    assert extract_note_field("PDF:", "PDF") is None


def test_extract_note_field_first_part() -> None:
    """When label appears as first segment."""
    assert extract_note_field("Abstract: http://example.com/abs", "Abstract") == "http://example.com/abs"


# ── _parse_note_text ─────────────────────────────────────────────

def test_parse_note_text_pdf_prefix_returns_none() -> None:
    """Note that starts with PDF: should return None."""
    assert _parse_note_text("PDF: http://example.com/a.pdf") is None


def test_parse_note_text_abstract_prefix_returns_none() -> None:
    """Note that starts with Abstract: should return None."""
    assert _parse_note_text("Abstract: http://example.com/abs") is None


def test_parse_note_text_normal_note_returned() -> None:
    """Normal text note without PDF:/Abstract: prefix is returned as-is."""
    assert _parse_note_text("This is a note") == "This is a note"


def test_parse_note_text_pdf_prefix_in_middle() -> None:
    """PDF: after the first segment is not stripped."""
    result = _parse_note_text("My note | PDF: http://x.com")
    assert result == "My note"


def test_parse_note_text_none_input() -> None:
    """None input returns None."""
    assert _parse_note_text(None) is None


def test_parse_note_text_whitespace_input() -> None:
    """Whitespace-only input → _empty_to_none returns None."""
    assert _parse_note_text("   ") is None


# ── _build_note ──────────────────────────────────────────────────

def test_build_note_combines_segments() -> None:
    """Segments joined with ' | '."""
    record = {"note": "My note", "pdf_url": "http://a.pdf", "abstract_url": "http://abs"}
    assert _build_note(record) == "My note | PDF: http://a.pdf | Abstract: http://abs"


def test_build_note_only_pdf() -> None:
    """Only pdf_url produces a note."""
    assert _build_note({"note": None, "pdf_url": "http://a.pdf"}) == "PDF: http://a.pdf"


def test_build_note_only_abstract() -> None:
    """Only abstract_url produces a note."""
    assert _build_note({"abstract_url": "http://abs"}) == "Abstract: http://abs"


def test_build_note_empty_all() -> None:
    """No segments → None."""
    assert _build_note({}) is None


# ── record_to_bibtex_entry edge → empty authors list ─────────────

def test_record_to_bibtex_empty_authors() -> None:
    """Empty authors list → no author field."""
    entry = record_to_bibtex_entry({"citekey": "test2024", "authors": []})
    assert "author" not in entry["fields"]


# ── bibtex_entry_to_record edge → arxiv without archiveprefix ───

def test_bibtex_entry_to_record_arxiv_no_prefix() -> None:
    """arxiv_id present without explicit archiveprefix → still captured (line 103)."""
    entry = {"entry_type": "article", "citekey": "x", "fields": {"eprint": "2401.12345"}}
    record = bibtex_entry_to_record(entry)
    assert record["arxiv_id"] == "2401.12345"


def test_bibtex_entry_to_record_arxiv_empty_prefix() -> None:
    """archiveprefix is not 'arXiv' and eprint empty → arxiv_id is None."""
    entry = {"entry_type": "article", "citekey": "x", "fields": {"archiveprefix": "foo"}}
    record = bibtex_entry_to_record(entry)
    assert record["arxiv_id"] is None


def test_bibtex_entry_to_record_empty_everything() -> None:
    """Minimal entry with no fields."""
    entry = {"entry_type": "article", "citekey": "min", "fields": {}}
    record = bibtex_entry_to_record(entry)
    assert record["citekey"] == "min"
    assert record["title"] is None
    assert record["authors"] == []
    assert record["year"] is None
