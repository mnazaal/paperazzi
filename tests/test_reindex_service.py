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


def test_reindex_preserves_comments_and_repoints_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "writer.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        old_pdf = os.path.join(papers, "oldkey.pdf")
        Path(old_pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            "% top comment\n"
            f"@article{{oldkey, title = {{New Test}}, author = {{Doe, John}}, "
            f"year = {{2025}}, file = {{{old_pdf}}}}}",
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert result["status"] == "ok"
        new_key = result["changed"][0]["new_citekey"]
        new_pdf = os.path.join(papers, f"{new_key}.pdf")
        # PDF is renamed on disk and the old name is gone.
        assert os.path.exists(new_pdf)
        assert not os.path.exists(old_pdf)
        content = Path(bib).read_text()
        # Comment preserved, and file= repointed at the renamed PDF (no dangling ref).
        assert "% top comment" in content
        assert new_pdf in content
        assert "oldkey.pdf" not in content


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


def test_reindex_renames_the_entrys_own_pdf_not_a_stray_namesake() -> None:
    # The PDF to rename comes from the entry's file= field.  A stray file that
    # happens to be named <old_citekey>.pdf belongs to nobody and must be left
    # alone; renaming it would attach the wrong PDF and orphan the real one.
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "stray.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        real_pdf = os.path.join(papers, "real-paper.pdf")
        Path(real_pdf).write_bytes(b"%PDF-1.4\nREAL\n")
        stray_pdf = os.path.join(papers, "oldkey.pdf")
        Path(stray_pdf).write_bytes(b"%PDF-1.4\nSTRAY\n")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},'
            f' file = {{{real_pdf}}}}}',
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        new_citekey = result["changed"][0]["new_citekey"]
        new_pdf = os.path.join(papers, f"{new_citekey}.pdf")
        assert Path(new_pdf).read_bytes() == b"%PDF-1.4\nREAL\n"
        assert Path(stray_pdf).read_bytes() == b"%PDF-1.4\nSTRAY\n"
        assert new_pdf in Path(bib).read_text()


def test_reindex_rolls_back_renamed_pdfs_when_the_bib_write_fails() -> None:
    # PDFs are renamed and the bib is written as one operation.  If the write
    # fails, every rename must be undone, or the library is left with file=
    # fields pointing at paths that no longer exist.
    import pytest

    from pzi import reindex_service

    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "rollback.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        old_pdf = os.path.join(papers, "oldkey.pdf")
        Path(old_pdf).write_bytes(b"%PDF-1.4\n")
        source = (
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},'
            f' file = {{{old_pdf}}}}}'
        )
        _write_bib(bib, source)

        def _boom(*args, **kwargs):
            raise ValueError("bib changed underneath us")

        original = reindex_service.rewrite_entries_in_order_locked
        reindex_service.rewrite_entries_in_order_locked = _boom
        try:
            with pytest.raises(ValueError):
                reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)
        finally:
            reindex_service.rewrite_entries_in_order_locked = original

        assert Path(old_pdf).read_bytes() == b"%PDF-1.4\n"
        assert Path(bib).read_text() == source


def test_reindex_refuses_to_overwrite_an_existing_pdf_at_the_new_path() -> None:
    # os.rename replaces the destination silently; a file already sitting at the
    # planned path must survive, with the collision reported.
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "clobber.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        real_pdf = os.path.join(papers, "real-paper.pdf")
        Path(real_pdf).write_bytes(b"%PDF-1.4\nREAL\n")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},'
            f' file = {{{real_pdf}}}}}',
        )
        # Occupy the path the rename would target.
        planned = os.path.join(papers, "doe2025new.pdf")
        Path(planned).write_bytes(b"%PDF-1.4\nOTHER\n")

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert result["changed"][0]["new_citekey"] == "doe2025new"
        assert Path(planned).read_bytes() == b"%PDF-1.4\nOTHER\n"
        assert Path(real_pdf).read_bytes() == b"%PDF-1.4\nREAL\n"
        assert result["errors"]


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
