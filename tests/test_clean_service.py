"""Tests for pzi.clean_service — library integrity checks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pzi.clean_service import clean_library, validate_library


def _write_bib(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


def test_validate_library_empty_bib_is_ok() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        papers = os.path.join(td, "papers")
        _write_bib(bib, "")
        result = validate_library(bib_path=bib, papers_dir=papers)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["issues"] == []


def test_validate_library_no_issues() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "clean.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {Smith}, year = {2024}}',
        )
        result = validate_library(bib_path=bib, papers_dir=papers)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        assert result["duplicate_citekeys"] == []
        assert result["missing_pdfs"] == []
        assert result["orphan_pdfs"] == []
        assert result["issues"] == []


def test_validate_library_duplicate_citekeys() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "dup.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            (
                '@article{smith2024, title = {A}, author = {Smith}, year = {2024}}\n'
                '@article{smith2024, title = {B}, author = {Jones}, year = {2023}}'
            ),
        )
        result = validate_library(bib_path=bib, papers_dir=papers)
        assert result["status"] == "ok"
        # bibtexparser v2 detects duplicates; at least 1 entry parsed, duplicate caught by parse
        assert result["total_entries"] >= 1
        # Duplicate citekeys appear as parse issues in bibtexparser v2
        issues = result["issues"]
        assert any(
            i["type"] in ("duplicate_citekey", "parse_error") for i in issues
        )


def test_validate_library_missing_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "missing.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {S}, year = {2024},'
            f' file = {{{papers}/nonexistent.pdf}}}}',
        )
        result = validate_library(bib_path=bib, papers_dir=papers)
        assert result["status"] == "ok"
        assert len(result["missing_pdfs"]) >= 1
        assert any(i["type"] == "missing_pdf" for i in result["issues"])


def test_validate_library_orphan_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "orphan.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        # Create orphan PDF
        orphan = os.path.join(papers, "orphan.pdf")
        Path(orphan).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {S}, year = {2024}}',
        )
        result = validate_library(bib_path=bib, papers_dir=papers)
        assert result["status"] == "ok"
        assert len(result["orphan_pdfs"]) >= 1
        assert any(i["type"] == "orphan_pdf" for i in result["issues"])


def test_clean_library_does_not_rewrite_bib() -> None:
    # clean --fix must never touch the .bib file (only relocate orphan PDFs),
    # so comments/@string/@preamble and source formatting are preserved.
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "sort-real.bib")
        papers = os.path.join(td, "papers")
        original = (
            '@article{zeta2024, title = {Z}, author = {Z}, year = {2024}}\n'
            '@article{alpha2023, title = {A}, author = {A}, year = {2023}}'
        )
        _write_bib(bib, original)

        result = clean_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert result["status"] == "ok"
        # No sort action, and the file is byte-for-byte unchanged.
        assert not any(a["type"] == "sort_entries" for a in result.get("actions", []))
        assert Path(bib).read_text() == original


def test_clean_library_move_orphans_dry_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "orphan2.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        orphan = os.path.join(papers, "stale.pdf")
        Path(orphan).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {S}, year = {2024}}',
        )
        result = clean_library(
            bib_path=bib, papers_dir=papers,
            dry_run=True, move_orphans=True,
        )
        assert result["status"] == "ok"
        actions = result.get("actions", [])
        assert any(a["type"] == "move_orphan" for a in actions)
        # Orphan should still exist (dry run)
        assert os.path.exists(orphan)


def test_clean_library_move_orphans_real() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "orphan3.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        orphan = os.path.join(papers, "stale.pdf")
        Path(orphan).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {S}, year = {2024}}',
        )
        result = clean_library(
            bib_path=bib, papers_dir=papers,
            dry_run=False, move_orphans=True,
        )
        assert result["status"] == "ok"
        actions = result.get("actions", [])
        assert any(a["type"] == "move_orphan" and a.get("done") for a in actions)
        # Orphan should be moved
        assert not os.path.exists(orphan)
        assert os.path.exists(os.path.join(papers, ".orphans", "stale.pdf"))
