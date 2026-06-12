"""Tests for pzi.reindex_service — citekey regeneration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pzi.reindex_service import reindex_library


def _write_bib(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


def test_reindex_empty_library() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        papers = os.path.join(td, "papers")
        _write_bib(bib, "")
        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["changed"] == []


def test_reindex_no_change_needed() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "clean.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            '@article{smith2024test, title = {Test}, author = {Smith}, year = {2024}}',
        )
        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)
        assert result["status"] == "ok"
        assert result["total_entries"] == 1
        assert result["changed"] == []


def test_reindex_changes_citekey_dry_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "change.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025}}',
        )
        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)
        assert result["status"] == "ok"
        assert len(result["changed"]) >= 1
        assert result["changed"][0]["old_citekey"] == "oldkey"
        assert result["changed"][0]["new_citekey"] != "oldkey"
        # File unchanged
        content = Path(bib).read_text()
        assert "oldkey" in content


def test_reindex_changes_citekey_real() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "change2.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025}}',
        )
        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)
        assert result["status"] == "ok"
        assert len(result["changed"]) >= 1
        assert result["changed"][0]["new_citekey"] != "oldkey"
        # File changed
        content = Path(bib).read_text()
        assert "oldkey" not in content
        assert result["changed"][0]["new_citekey"] in content


def test_reindex_renames_pdf_dry_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "rename.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        old_pdf = os.path.join(papers, "oldkey.pdf")
        Path(old_pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},'
            f' file = {{{old_pdf}}}}}',
        )
        result = reindex_library(
            bib_path=bib, papers_dir=papers,
            dry_run=True,
        )
        assert result["status"] == "ok"
        changed = result["changed"]
        assert len(changed) >= 1
        # PDF not moved (dry run)
        assert os.path.exists(old_pdf)


def test_reindex_renames_pdf_real() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "rename2.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        old_pdf = os.path.join(papers, "oldkey.pdf")
        Path(old_pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},'
            f' file = {{{old_pdf}}}}}',
        )
        result = reindex_library(
            bib_path=bib, papers_dir=papers,
            dry_run=False,
        )
        assert result["status"] == "ok"
        changed = result["changed"]
        assert len(changed) >= 1
        new_citekey = changed[0]["new_citekey"]
        # Old PDF should be renamed
        assert not os.path.exists(old_pdf)
        assert os.path.exists(os.path.join(papers, f"{new_citekey}.pdf"))


def test_reindex_collision_avoids_duplicate() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "collide.bib")
        papers = os.path.join(td, "papers")
        # Entry B generates citekey equal to Entry A's citekey
        _write_bib(
            bib,
            (
                '@article{doe2025test, title = {A Test}, author = {Doe}, year = {2025}}\n'
                '@article{badkey, title = {Test}, author = {Doe, John}, year = {2025}}'
            ),
        )
        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)
        assert result["status"] == "ok"
        changed = result["changed"]
        assert len(changed) >= 1
        # The bad citekey should change but NOT collide with the first one
        for ch in changed:
            assert ch["new_citekey"] != "doe2025test" or ch["old_citekey"] == "doe2025test"
