"""Tests for pzi.export_service — all four export formats."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pzi.export_service import (
    export_bibtex,
    export_csv,
    export_json,
    export_ris,
)


def _write_bib(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


SIMPLE_BIB = (
    '@article{smith2024,\n'
    '  title = {Deep Learning},\n'
    '  author = {Smith, John and Jones, Alice},\n'
    '  year = {2024},\n'
    '  doi = {10.1000/example},\n'
    '  journal = {Nature},\n'
    '}\n'
)


def test_export_bibtex_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_bibtex(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "bibtex"
        assert result["content_type"] == "application/x-bibtex"


def test_export_bibtex_content() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_bibtex(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        assert "smith2024" in result["content"]
        assert "Deep Learning" in result["content"]


def test_export_json_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_json(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "json"
        assert result["content_type"] == "application/json"
        parsed = json.loads(result["content"])
        assert parsed == []


def test_export_json_records() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_json(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        parsed = json.loads(result["content"])
        assert len(parsed) == 1
        rec = parsed[0]
        assert rec["title"] == "Deep Learning"
        assert rec["doi"] == "10.1000/example"
        assert rec["year"] == 2024
        assert rec["entry_type"] == "article"
        assert "Smith, John" in rec["authors"]
        assert "Jones, Alice" in rec["authors"]


def test_export_csv_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_csv(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "csv"
        assert result["content_type"] == "text/csv"
        # Should have header only
        lines = result["content"].strip().split("\n")
        assert len(lines) == 1  # header only
        assert "citekey" in lines[0]


def test_export_csv_records() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_csv(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        lines = result["content"].strip().split("\n")
        assert len(lines) == 2  # header + 1 row
        assert "smith2024" in lines[1]
        assert "Deep Learning" in lines[1]


def test_export_ris_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = export_ris(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["format"] == "ris"
        assert result["content"] == ""


def test_export_ris_records() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, SIMPLE_BIB)
        result = export_ris(bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        assert result["content_type"] == "application/x-research-info-systems"
        ris = result["content"]
        assert "TY  - JOUR" in ris
        assert "TI  - Deep Learning" in ris
        # Title must be emitted exactly once per entry (regression: was duplicated).
        assert ris.count("TI  - ") == 1
        assert "AU  - Smith, John" in ris
        assert "AU  - Jones, Alice" in ris
        assert "DO  - 10.1000/example" in ris
        assert "PY  - 2024" in ris
        assert "ER  - " in ris


def test_export_ris_inproceedings_type() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(
            bib,
            '@inproceedings{conf2024, title = {Paper}, author = {A}, year = {2024}}',
        )
        result = export_ris(bib)
        assert "TY  - CONF" in result["content"]


def test_normalize_authors_handles_list_string_and_other() -> None:
    from pzi.export_service import _normalize_authors

    assert _normalize_authors(["Smith, J", "Doe, A"]) == "Smith, J; Doe, A"
    assert _normalize_authors("Smith, J and Doe, A") == "Smith, J and Doe, A"
    assert _normalize_authors(None) == ""


def test_normalize_tags_handles_list_string_and_other() -> None:
    from pzi.export_service import _normalize_tags

    assert _normalize_tags(["ml", "graphs"]) == "ml, graphs"
    assert _normalize_tags("ml,graphs") == "ml,graphs"
    assert _normalize_tags(None) == ""


def test_export_reports_entries_the_parser_dropped() -> None:
    """An export that silently omits entries is not a backup.

    bibtexparser v2 collects unreadable blocks instead of raising, so every
    exporter used to return the entries that happened to parse with
    `status: ok` and no errors — losing the rest with exit 0.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(
            bib,
            "@article{good2024, title = {Good}, author = {A}, year = {2024}}\n\n"
            "@article{broken2024,\n  title = {Unclosed\n  year = {2024},\n}\n",
        )

        for export in (export_bibtex, export_json, export_csv, export_ris):
            result = export(bib)
            assert result["status"] == "error", export.__name__
            assert result["errors"], export.__name__
            assert "unparseable" in result["errors"][0].lower(), export.__name__


def test_export_reports_a_duplicate_citekey_as_a_dropped_entry() -> None:
    """Only the first block of a duplicated key parses; the second is lost too."""
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(
            bib,
            "@article{dup2024, title = {A}}\n@article{dup2024, title = {B}}\n",
        )

        result = export_bibtex(bib)

        assert result["status"] == "error"
        assert any("duplicate citekey" in err for err in result["errors"])


def test_export_of_a_clean_library_stays_ok() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "test.bib")
        _write_bib(bib, '@article{good2024, title = {Good}, year = {2024}}')

        result = export_bibtex(bib)

        assert result["status"] == "ok"
        assert result["errors"] == []
