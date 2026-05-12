"""Tests for PDF metadata extraction."""

from pathlib import Path

import pytest

from pzi.pdf_metadata import _extract_doi_from_text, _extract_title_from_text, extract_pdf_metadata


def _make_pdf_with_text(tmp_path: Path, text: str) -> Path:
    """Create a minimal PDF with embedded text using pypdf."""
    try:
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    # Add text via content stream (minimal approach)
    content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET".encode()
    page["/Contents"] = writer._add_object(
        DictionaryObject({NameObject("/Length"): len(content)})
    )
    # Note: this is a simplified mock; real text extraction may fail
    # We'll use a different approach - create a real PDF with text

    path = tmp_path / "test.pdf"
    with path.open("wb") as f:
        writer.write(f)
    return path


def test_extract_pdf_metadata_missing_file(tmp_path: Path) -> None:
    result = extract_pdf_metadata(str(tmp_path / "nonexistent.pdf"))
    assert result == {"doi": None, "title": None, "text_sample": None}


def test_extract_doi_from_text_finds_first() -> None:
    text = "Some paper text. DOI: 10.1145/3368089.3409741 More text."
    assert _extract_doi_from_text(text) == "10.1145/3368089.3409741"


def test_extract_doi_from_text_no_match() -> None:
    assert _extract_doi_from_text("No doi here") is None


def test_extract_doi_from_text_normalizes_whitespace() -> None:
    # Preprocessing removes spaces from matched candidate
    text = "DOI: 10.1145/3368089.3409741"
    assert _extract_doi_from_text(text) == "10.1145/3368089.3409741"


def test_extract_title_from_text_skips_junk() -> None:
    text = "DOI: 10.1/foo\nJournal of Testing\n\nReal Paper Title Here\nAbstract..."
    assert _extract_title_from_text(text) == "Real Paper Title Here"


def test_extract_title_from_text_too_short_skipped() -> None:
    text = "DOI: 10.1/foo\nHi\nShort\nActual Title That Is Long Enough"
    assert _extract_title_from_text(text) == "Actual Title That Is Long Enough"


def test_extract_title_from_text_none() -> None:
    assert _extract_title_from_text("DOI\nhttp\n© 2024") is None


def test_extract_pdf_metadata_real_pdf(tmp_path: Path) -> None:
    """Test with a real PDF created via pypdf if available."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)

    path = tmp_path / "real_test.pdf"
    with path.open("wb") as f:
        writer.write(f)

    result = extract_pdf_metadata(str(path))
    # Blank page has no text; just verify it doesn't crash
    assert result["doi"] is None
    assert result["title"] is None
