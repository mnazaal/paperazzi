"""One item's rollback must not delete another item's PDF — audit C9.

Two entries can legitimately share one file. `resolve_pdf_destination` returns
an *existing* path when the bytes are identical, and two entries plan the same
filename whenever author/year/title render alike — in the user's real 23k
library that is 33 filenames across 67 entries, all preprint/published pairs
and duplicate imports, which are exactly the entries whose PDFs are
byte-identical.

Both batch paths took a single snapshot of `papers_dir` before the loop and
rolled back against it, so every file the batch created looked removable to
every later item. A later item that failed to write its entry would unlink the
file an earlier, already-committed entry pointed at: a live entry referencing a
deleted PDF, reported by nothing.

The audit named `pdf_service.retry_failed_pdfs`. `add_service` has the same
shape at its per-record `except`, and is covered here too — fixing one call
site and not its sibling is this project's dominant defect.
"""

from __future__ import annotations

from pathlib import Path

from pzi.pdf import remove_new_pdf, snapshot_pdf_paths


def test_remove_new_pdf_spares_a_file_another_item_committed(tmp_path: Path) -> None:
    """The rule, stated directly on the shared helper both paths call."""
    papers = tmp_path / "papers"
    papers.mkdir()
    shared = papers / "smith-2020-a-paper.pdf"
    shared.write_bytes(b"%PDF-1.7\nshared\n")

    # Snapshot taken *before* this file existed, as a pre-batch snapshot is.
    before: set[Path] = set()

    remove_new_pdf(str(shared), before, keep=[str(shared)])
    assert shared.exists(), "a committed item's PDF was deleted by a later rollback"

    # Without the claim it is still removed — the guard is `keep`, not luck.
    remove_new_pdf(str(shared), before)
    assert not shared.exists()


def test_remove_new_pdf_still_removes_a_genuinely_new_file(tmp_path: Path) -> None:
    """`keep` must not turn the rollback off for the file it is meant to remove."""
    papers = tmp_path / "papers"
    papers.mkdir()
    committed = papers / "committed.pdf"
    committed.write_bytes(b"%PDF-1.7\ncommitted\n")
    orphan = papers / "orphan.pdf"
    orphan.write_bytes(b"%PDF-1.7\norphan\n")

    remove_new_pdf(str(orphan), set(), keep=[str(committed)])
    assert not orphan.exists(), "the failed item's own PDF should still be cleaned up"
    assert committed.exists()


def test_remove_new_pdf_still_spares_pre_existing_files(tmp_path: Path) -> None:
    """The original guarantee is unchanged: a file older than the batch stays."""
    papers = tmp_path / "papers"
    papers.mkdir()
    old = papers / "old.pdf"
    old.write_bytes(b"%PDF-1.7\nold\n")

    remove_new_pdf(str(old), snapshot_pdf_paths(str(papers)))
    assert old.exists()


def test_both_batch_paths_pass_their_claimed_files_to_the_rollback() -> None:
    """C9's point: the two batch loops must not diverge on this rule.

    `pdf_service.retry_failed_pdfs` commits per item and accumulates
    `committed_pdfs`; `add_service.add_records_to_bib_batch` applies to a
    session and accumulates `batch_pdfs`. Both must hand their list to
    `remove_new_pdf` as `keep`, or that path silently keeps the old bug.
    """
    import inspect

    from pzi.add_service import add_records_to_bib_batch
    from pzi.pdf_service import retry_failed_pdfs

    retry_src = inspect.getsource(retry_failed_pdfs)
    assert "committed_pdfs" in retry_src
    assert "keep=committed_pdfs" in retry_src, "retry rollback ignores committed files"

    batch_src = inspect.getsource(add_records_to_bib_batch)
    assert "keep=batch_pdfs" in batch_src, "batch rollback ignores applied records"


def test_the_outer_batch_cleanup_still_removes_every_downloaded_pdf() -> None:
    """The one place `keep` must NOT be passed, pinned so it is not "tidied" in.

    `add_records_to_bib_batch`'s outer handler runs when the whole batch failed
    to commit — nothing was written, so every PDF it downloaded is an orphan
    and all of them must go, including the ones records had claimed. Passing
    `keep=batch_pdfs` there would leak exactly the files it exists to remove.
    """
    import inspect

    from pzi.add_service import add_records_to_bib_batch

    source = inspect.getsource(add_records_to_bib_batch)
    outer = source.split("except Exception:")[-1]
    assert "for pdf_path in batch_pdfs:" in outer
    assert "keep=" not in outer, (
        "the whole-batch cleanup must not spare claimed files — nothing was written"
    )
