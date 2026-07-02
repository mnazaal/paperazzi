"""End-to-end integration smoke tests for the pzi pipeline.

Tests compose multiple services together using temporary bib files,
exercising the full add → validate → export → delete lifecycle.
No external services (translation server, Playwright) are required.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pzi.add_service import add_record_to_bib
from pzi.bib_repository import read_bib_file
from pzi.bib_service import delete_entry
from pzi.clean_service import validate_library
from pzi.export_service import export_bibtex, export_json


def _make_record(citekey, title, year, doi=None, authors=None):
    """Build a minimal NormalizedRecord."""
    return {
        "citekey": citekey,
        "title": title,
        "year": year,
        "doi": doi,
        "authors": authors or [],
        "tags": [],
    }


# ── Pipeline integration tests ──────────────────────────────────────────────


def test_add_record_and_read_back(write_app_config) -> None:
    """Add a record, then read it back through read_bib_file."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        bib_path = os.path.join(td, "main.bib")

        result = add_record_to_bib(
            config_path=config_path,
            home_dir=td,
            record=_make_record("smith2024great", "A Great Paper", 2024,
                                doi="10.1234/test.001"),
            bib_selector=None,
            dry_run=False,
        )
        assert result["status"] == "ok"
        assert result["citekey"] == "smith2024great"

        # Read back
        data = read_bib_file(bib_path)
        assert len(data["records"]) == 1
        assert data["records"][0]["title"] == "A Great Paper"
        assert data["records"][0]["doi"] == "10.1234/test.001"


def test_add_and_validate(write_app_config) -> None:
    """Add a record, then validate the library."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        papers_dir = os.path.join(td, "papers")
        bib_path = os.path.join(td, "main.bib")

        add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("test2024", "Test Title", 2024),
            bib_selector=None, dry_run=False,
        )

        validation = validate_library(bib_path=bib_path, papers_dir=papers_dir)
        assert validation["status"] == "ok"
        assert validation["total_entries"] == 1
        assert validation["issues"] == []


def test_add_and_export_json(write_app_config) -> None:
    """Add a record, then export as JSON."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        bib_path = os.path.join(td, "main.bib")

        add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("export2024", "Export Me", 2024,
                                doi="10.5678/export.1", authors=["Doe, John", "Smith, Alice"]),
            bib_selector=None, dry_run=False,
        )

        export = export_json(bib_path=bib_path)
        assert export["status"] == "ok"
        assert export["total_entries"] == 1
        records = json.loads(export["content"])
        assert len(records) == 1
        # JSON export includes entry_type
        record = records[0]
        assert "entry_type" in record
        assert record.get("title") == "Export Me"
        assert record.get("doi") == "10.5678/export.1"


def test_add_and_export_bibtex(write_app_config) -> None:
    """Add a record, then export as BibTeX."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        bib_path = os.path.join(td, "main.bib")

        add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("bibexport2024", "BibTeX Export", 2024),
            bib_selector=None, dry_run=False,
        )

        export = export_bibtex(bib_path=bib_path)
        assert export["status"] == "ok"
        assert export["total_entries"] == 1
        assert "bibexport2024" in export["content"]
        assert "BibTeX Export" in export["content"]


def test_add_dedupe_by_doi(write_app_config) -> None:
    """Add two records with same DOI — second should merge/update."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        bib_path = os.path.join(td, "main.bib")

        # First add
        r1 = add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("first2024", "First Version", 2024,
                                doi="10.1234/dup.001"),
            bib_selector=None, dry_run=False,
        )
        assert r1["citekey"] == "first2024"

        # Second add — same DOI, should merge
        _r2 = add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("second2024", "Second Version", 2024,
                                doi="10.1234/dup.001"),
            bib_selector=None, dry_run=False,
        )
        # Should update the existing entry, not create new one
        data = read_bib_file(bib_path)
        assert len(data["records"]) == 1  # one entry, deduped


def test_add_delete_verify_gone(write_app_config) -> None:
    """Add a record, delete it, verify it is gone."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        bib_path = os.path.join(td, "main.bib")

        add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("todelete2024", "Delete Me", 2024),
            bib_selector=None, dry_run=False,
        )

        # Delete
        del_result = delete_entry(
            bib_path=bib_path, citekey="todelete2024", dry_run=False,
        )
        assert del_result["status"] == "ok"

        # Verify empty
        data = read_bib_file(bib_path)
        assert len(data["records"]) == 0


def test_add_orphan_pdf_detected(write_app_config) -> None:
    """Create an orphan PDF, run validate, verify detection."""
    with tempfile.TemporaryDirectory() as td:
        config_path = write_app_config(
            td, bib_name="main", contact_email="test@example.com",
            metadata_confidence_min_score=0,
        )
        papers_dir = os.path.join(td, "papers")
        bib_path = os.path.join(td, "main.bib")
        os.makedirs(papers_dir, exist_ok=True)

        # Add a record (no PDF)
        add_record_to_bib(
            config_path=config_path, home_dir=td,
            record=_make_record("nopdf2024", "No PDF", 2024),
            bib_selector=None, dry_run=False,
        )

        # Create orphan PDF
        orphan = os.path.join(papers_dir, "stale.pdf")
        Path(orphan).write_bytes(b"%PDF-1.4 test\n")

        validation = validate_library(bib_path=bib_path, papers_dir=papers_dir)
        assert validation["status"] == "ok"
        assert len(validation["orphan_pdfs"]) >= 1
        assert any(i["type"] == "orphan_pdf" for i in validation["issues"])
