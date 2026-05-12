"""Edge tests for pzi.pdf_metadata covering previously uncovered branches.

Covers missing lines in extract_pdf_metadata (ImportError, missing file,
corrupt PDF, page extraction errors, empty text, etc.).

Also covers edge cases in _extract_doi_from_text and _extract_title_from_text.
"""

from pathlib import Path

import pytest

from pzi.pdf_metadata import _extract_doi_from_text, _extract_title_from_text, extract_pdf_metadata

# ---------------------------------------------------------------------------
# extract_pdf_metadata: ImportError (pypdf not installed)
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_pypdf_not_installed(monkeypatch, tmp_path: Path) -> None:
    """If pypdf is not importable, return empty result."""
    import builtins
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pypdf" or name.startswith("pypdf."):
            raise ImportError("No module named pypdf")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    result = extract_pdf_metadata(str(tmp_path / "irrelevant.pdf"))
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# extract_pdf_metadata: missing file
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_missing_file(tmp_path: Path) -> None:
    """File does not exist → returns None values."""
    result = extract_pdf_metadata(str(tmp_path / "nonexistent.pdf"))
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# extract_pdf_metadata: corrupt / unreadable PDF (OSError or ValueError on PdfReader)
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_corrupt_pdf(tmp_path: Path, monkeypatch) -> None:
    """PdfReader raises OSError → returns empty result."""
    fpath = tmp_path / "corrupt.pdf"
    fpath.write_bytes(b"%PDF-1.4 garbage")

    try:
        from pypdf import PdfReader as _  # noqa: F401
    except ImportError:
        pytest.skip("pypdf not installed")

    def bad_reader(path):
        raise OSError("corrupt PDF")

    monkeypatch.setattr(
        "pypdf.PdfReader",
        bad_reader,
    )
    result = extract_pdf_metadata(str(fpath))
    assert result == {"doi": None, "title": None, "text_sample": None}


def test_extract_pdf_metadata_value_error(tmp_path: Path, monkeypatch) -> None:
    """PdfReader raises ValueError → returns empty result."""
    fpath = tmp_path / "bad.pdf"
    fpath.write_bytes(b"not a pdf at all")

    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    def bad_reader(path):
        raise ValueError("invalid PDF")

    monkeypatch.setattr(
        "pypdf.PdfReader",
        bad_reader,
    )
    result = extract_pdf_metadata(str(fpath))
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# extract_pdf_metadata: pages that raise on extract_text
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_page_extraction_error(tmp_path: Path, monkeypatch) -> None:
    """Page.extract_text raises OSError/ValueError → page is skipped."""
    fpath = tmp_path / "bad_page.pdf"
    fpath.write_bytes(b"%PDF-1.4")

    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    class ErrorPageReader:
        def __init__(self, path):
            pass

        @property
        def pages(self):
            return [_BadPage()]

    class _BadPage:
        def extract_text(self):
            raise OSError("cannot extract text")

    monkeypatch.setattr(
        "pypdf.PdfReader",
        ErrorPageReader,
    )

    result = extract_pdf_metadata(str(fpath))
    # All pages errored, no text extracted → empty result
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# extract_pdf_metadata: some pages succeed, some fail
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_mixed_pages(tmp_path: Path, monkeypatch) -> None:
    """One page extracts successfully, another raises → partial text used."""

    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    class MixedReader:
        def __init__(self, path):
            pass

        @property
        def pages(self):
            return [_GoodPage(), _BadPage(), _GoodPage()]

    class _GoodPage:
        def extract_text(self):
            return "Page 1 text with DOI: 10.1145/3368089.3409741"

    class _BadPage:
        def extract_text(self):
            raise ValueError("bad page")

    monkeypatch.setattr(
        "pypdf.PdfReader",
        MixedReader,
    )

    fpath = tmp_path / "mixed.pdf"
    fpath.write_bytes(b"%PDF-1.4")

    result = extract_pdf_metadata(str(fpath))
    assert result["doi"] == "10.1145/3368089.3409741"
    # "Page 1 text..." starts with "page" which is a skip_prefix
    # for title extraction, so title will be None
    assert result["title"] is None
    assert result["text_sample"] is not None
    assert "Page 1 text" in result["text_sample"]


# ---------------------------------------------------------------------------
# extract_pdf_metadata: all pages return None or empty text
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_all_pages_empty(tmp_path: Path, monkeypatch) -> None:
    """Every page returns None or '' from extract_text → empty result."""

    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    class AllEmptyReader:
        def __init__(self, path):
            pass

        @property
        def pages(self):
            return [_NonePage(), _EmptyPage(), _SpacePage()]

    class _NonePage:
        def extract_text(self):
            return None

    class _EmptyPage:
        def extract_text(self):
            return ""

    class _SpacePage:
        def extract_text(self):
            return "   \t\n  "

    monkeypatch.setattr(
        "pypdf.PdfReader",
        AllEmptyReader,
    )

    fpath = tmp_path / "empty.pdf"
    fpath.write_bytes(b"%PDF-1.4")

    result = extract_pdf_metadata(str(fpath))
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# extract_pdf_metadata: page extract_text raises AttributeError
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_attribute_error(tmp_path: Path, monkeypatch) -> None:
    """Page has no extract_text method → AttributeError caught."""

    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    class NoMethodReader:
        def __init__(self, path):
            pass

        @property
        def pages(self):
            return [_NoMethodPage()]

    class _NoMethodPage:
        pass  # no extract_text method

    monkeypatch.setattr(
        "pypdf.PdfReader",
        NoMethodReader,
    )

    fpath = tmp_path / "no_method.pdf"
    fpath.write_bytes(b"%PDF-1.4")

    result = extract_pdf_metadata(str(fpath))
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# extract_pdf_metadata: full_text.join results in empty/whitespace
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_empty_full_text(tmp_path: Path, monkeypatch) -> None:
    """Pages join to empty string after strip → no text available."""

    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    class SpaceReader:
        def __init__(self, path):
            pass

        @property
        def pages(self):
            return [_NewlinePage()]

    class _NewlinePage:
        def extract_text(self):
            return "\n \n"

    monkeypatch.setattr(
        "pypdf.PdfReader",
        SpaceReader,
    )

    fpath = tmp_path / "spaces.pdf"
    fpath.write_bytes(b"%PDF-1.4")

    result = extract_pdf_metadata(str(fpath))
    assert result == {"doi": None, "title": None, "text_sample": None}


# ---------------------------------------------------------------------------
# _extract_doi_from_text: whitespace in DOI
# ---------------------------------------------------------------------------


def test_extract_doi_from_text_whitespace_before_doi() -> None:
    """DOI with surrounding whitespace gets normalized by the function."""
    # The DOI regex uses \S+ which doesn't match internal spaces.
    # Whitespace around the DOI is handled by .strip() and the regex anchors.
    text = "  DOI: 10.1145/3368089.3409741  "
    assert _extract_doi_from_text(text) == "10.1145/3368089.3409741"


def test_extract_doi_from_text_trailing_slash() -> None:
    """DOI ending with characters like semicolon still extracts."""
    text = "doi: 10.1145/3368089.3409741; see also"
    assert _extract_doi_from_text(text) == "10.1145/3368089.3409741"


# ---------------------------------------------------------------------------
# _extract_title_from_text: edge cases
# ---------------------------------------------------------------------------


def test_extract_title_from_text_skips_www() -> None:
    text = "www.example.com\nActual Title Here\nAbstract"
    assert _extract_title_from_text(text) == "Actual Title Here"


def test_extract_title_from_text_skips_conference() -> None:
    text = "Conference on Machine Learning\nReal Title Here\n2024"
    assert _extract_title_from_text(text) == "Real Title Here"


def test_extract_title_from_text_skips_received() -> None:
    text = "Received: 2024-01-01\nAccepted: 2024-06-01\nPublished: 2024-12-01\nBreakthrough Title Here"
    assert _extract_title_from_text(text) == "Breakthrough Title Here"


def test_extract_title_from_text_skips_issn_isbn() -> None:
    text = "ISSN 1234-5678\nISBN 978-0-12-345678-9\nProceedings of Foo\nThe Real Title Here"
    assert _extract_title_from_text(text) == "The Real Title Here"


def test_extract_title_from_text_skips_keywords_line() -> None:
    text = "keywords: machine learning, nlp\nImportant Discovery Title 2024"
    assert _extract_title_from_text(text) == "Important Discovery Title 2024"


def test_extract_title_from_text_skips_introduction() -> None:
    text = "Introduction\n\n1. Background\nActual Paper Title"
    assert _extract_title_from_text(text) == "1. Background"  # "1. Background" is >= 10 chars


def test_extract_title_from_text_skips_pp_and_page() -> None:
    text = "pp. 123-145\npage 42\nActual Title of The Paper"
    assert _extract_title_from_text(text) == "Actual Title of The Paper"


def test_extract_title_from_text_skips_vol() -> None:
    text = "vol. 42, no. 3\nA Real Title Here"
    assert _extract_title_from_text(text) == "A Real Title Here"


def test_extract_title_from_text_skips_fig_figure_table() -> None:
    text = "fig. 1: Overview\nfigure 2: Details\ntable 1: Results\nReal Title of The Paper"
    assert _extract_title_from_text(text) == "Real Title of The Paper"


def test_extract_title_from_text_dash_pattern_skipped() -> None:
    text = "---\nReal Title Here\n---"
    assert _extract_title_from_text(text) == "Real Title Here"


def test_extract_title_from_text_em_dash_skipped() -> None:
    text = "\u2014\u2014\u2014\nActual Title\nMore text"
    assert _extract_title_from_text(text) == "Actual Title"


def test_extract_title_from_text_asterisk_skipped() -> None:
    text = "*\n**\nReal Title of The Paper"
    assert _extract_title_from_text(text) == "Real Title of The Paper"


def test_extract_title_from_text_lone_number_skipped() -> None:
    text = "42\n\nReal Title of The Paper"
    assert _extract_title_from_text(text) == "Real Title of The Paper"


def test_extract_title_from_text_title_at_max_length() -> None:
    """Title exactly 200 chars is accepted."""
    title = "A" * 200
    assert _extract_title_from_text(title) == title


def test_extract_title_from_text_title_too_long() -> None:
    """Line > 200 chars is skipped, shorter title after is used."""
    text = "A" * 201 + "\nReal Title Here"
    assert _extract_title_from_text(text) == "Real Title Here"


def test_extract_title_from_text_starts_with_copyright() -> None:
    text = "© 2024 The Authors\nReal Title of The Paper"
    assert _extract_title_from_text(text) == "Real Title of The Paper"


def test_extract_title_from_text_abstract_skipped() -> None:
    text = "Abstract\n\nWe present a novel approach.\nReal Title of The Paper"
    # "Abstract" is in skip_prefixes, but "We present a novel approach" comes next
    # and it's >= 10 chars, so it's selected
    assert _extract_title_from_text(text) == "We present a novel approach."


# ---------------------------------------------------------------------------
# extract_pdf_metadata: text_sample truncated at 2000 chars
# ---------------------------------------------------------------------------


def test_extract_pdf_metadata_truncates_sample(tmp_path: Path, monkeypatch) -> None:
    """text_sample is capped at 2000 characters."""
    try:
        from pypdf import PdfReader as _
    except ImportError:
        pytest.skip("pypdf not installed")

    long_text = "X" * 3000 + " DOI: 10.1234/test"

    class LongReader:
        def __init__(self, path):
            pass

        @property
        def pages(self):
            return [_LongPage()]

    class _LongPage:
        def extract_text(self):
            return long_text

    monkeypatch.setattr(
        "pypdf.PdfReader",
        LongReader,
    )

    fpath = tmp_path / "long.pdf"
    fpath.write_bytes(b"%PDF-1.4")

    result = extract_pdf_metadata(str(fpath))
    assert result["doi"] == "10.1234/test"
    assert result["text_sample"] is not None
    assert len(result["text_sample"]) == 2000
    assert result["text_sample"] == "X" * 2000
