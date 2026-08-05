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
        # bibtexparser v2 reports a duplicate key as a failed block and keeps
        # only the first. That is a *readable* library with one reported entry
        # missing — a finding, not a refusal — so it is reported in the field
        # named for it rather than as a generic parse error.
        assert result["status"] == "ok"
        assert result["duplicate_citekeys"] == ["smith2024"]
        assert [i["type"] for i in result["issues"]] == ["duplicate_citekey"]
        assert "only the first occurrence is read" in result["issues"][0]["message"]
        # The counts describe a partial read, and that is flagged.
        assert result["partial_parse"] is True


def test_clean_fix_does_not_quarantine_the_pdf_of_a_dropped_duplicate() -> None:
    """The dropped half of a duplicate-citekey pair still owns its PDF.

    Only the first block of a duplicated key survives parsing, so the second
    one's PDF is referenced by nothing the parser can see — the same way an
    unparseable entry's is.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "dup_pdf.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        first_pdf = os.path.join(papers, "smith2024.pdf")
        second_pdf = os.path.join(papers, "smith2024-2.pdf")
        Path(first_pdf).write_bytes(b"%PDF-1.4\n")
        Path(second_pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            f'@article{{smith2024, title = {{A}}, file = {{{first_pdf}}}}}\n'
            f'@article{{smith2024, title = {{B}}, file = {{{second_pdf}}}}}\n',
        )

        result = clean_library(
            bib_path=bib, papers_dir=papers, dry_run=False, move_orphans=True,
        )

        # The guarantee is unchanged; only the status it rides on is. Duplicates
        # no longer produce an error status, so this is now protected by
        # `partial_parse` — which is exactly the regression this test exists to
        # catch.
        assert result["status"] == "ok"
        assert result["partial_parse"] is True
        assert result.get("actions") == []
        assert result["orphan_pdfs"] == []
        assert os.path.exists(second_pdf)
        assert os.path.exists(first_pdf)
        assert not os.path.exists(os.path.join(papers, ".orphans"))


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


def test_quarantined_pdfs_are_not_reported_as_orphans_again() -> None:
    # Files already moved into papers_dir/.orphans are quarantined, not loose
    # orphans.  Re-detecting them makes `pzi fix clean` exit non-zero forever
    # once anything has been quarantined.
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "requarantine.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        Path(os.path.join(papers, "stale.pdf")).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {S}, year = {2024}}',
        )

        clean_library(bib_path=bib, papers_dir=papers, dry_run=False, move_orphans=True)
        second = clean_library(
            bib_path=bib, papers_dir=papers, dry_run=False, move_orphans=True
        )

        assert second["orphan_pdfs"] == []
        assert not any(i["type"] == "orphan_pdf" for i in second["issues"])
        assert second.get("actions") == []


def test_quarantining_a_second_file_of_the_same_name_keeps_the_first() -> None:
    # The quarantine directory is an archive: a later orphan sharing a basename
    # must not overwrite the copy already stored there.
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "collide.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        orphan = os.path.join(papers, "stale.pdf")
        Path(orphan).write_bytes(b"%PDF-1.4\nFIRST\n")
        _write_bib(
            bib,
            '@article{smith2024, title = {Test}, author = {S}, year = {2024}}',
        )

        clean_library(bib_path=bib, papers_dir=papers, dry_run=False, move_orphans=True)
        # A different file arrives later under the same basename.
        Path(orphan).write_bytes(b"%PDF-1.4\nSECOND\n")
        clean_library(bib_path=bib, papers_dir=papers, dry_run=False, move_orphans=True)

        archived = sorted(
            p.read_bytes() for p in Path(papers, ".orphans").glob("*.pdf")
        )
        assert archived == [b"%PDF-1.4\nFIRST\n", b"%PDF-1.4\nSECOND\n"]


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


def test_validate_library_reports_a_wholly_corrupt_bib_as_an_error() -> None:
    """A bib the parser cannot read must not be reported as a clean, empty one.

    The lenient reader drops unparseable blocks rather than raising, so a fully
    corrupt file yields zero entries — indistinguishable from an empty library
    unless the parse is checked explicitly.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "corrupt.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        _write_bib(bib, "@article{broken2024,\n  title = {Unclosed brace\n")

        result = validate_library(bib_path=bib, papers_dir=papers)

        assert result["status"] == "error"
        assert [i["type"] for i in result["issues"]] == ["parse_error"]


def test_clean_fix_does_not_quarantine_pdfs_of_unparseable_entries() -> None:
    """A malformed entry's PDF is not an orphan — the parser just could not see it.

    Dropped entries contribute no referenced paths, so their PDFs look unclaimed.
    Moving one to `.orphans/` leaves the entry's own `file =` dangling, which is
    data loss caused by the repair tool.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "partial.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        good_pdf = os.path.join(papers, "good2024.pdf")
        broken_pdf = os.path.join(papers, "broken2024.pdf")
        Path(good_pdf).write_bytes(b"%PDF-1.4\n")
        Path(broken_pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            "@article{good2024,\n"
            "  title = {A Good One},\n"
            f"  file = {{{good_pdf}}},\n"
            "}\n"
            "\n"
            "@article{broken2024,\n"
            "  title = {Missing closing brace\n"
            f"  file = {{{broken_pdf}}},\n"
            "}\n",
        )

        result = clean_library(
            bib_path=bib, papers_dir=papers, dry_run=False, move_orphans=True,
        )

        assert result["status"] == "error"
        assert result.get("actions") == []
        # Neither PDF was moved.
        assert os.path.exists(broken_pdf)
        assert os.path.exists(good_pdf)
        assert not os.path.exists(os.path.join(papers, ".orphans"))


def test_validate_library_skips_orphan_detection_under_a_partial_parse() -> None:
    """Orphan detection needs the *complete* set of referenced paths.

    A dropped duplicate contributes none, so its PDF looks orphaned. Reporting
    it would be wrong, and `--fix` acting on the report would move a file the
    library still references. This is the primary guard; `clean_library`'s
    `partial_parse` check is a second lock on the same door.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "dup.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        second_pdf = os.path.join(papers, "smith2024-2.pdf")
        Path(second_pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            f'@article{{smith2024, title = {{A}}}}\n'
            f'@article{{smith2024, title = {{B}}, file = {{{second_pdf}}}}}\n',
        )

        result = validate_library(bib_path=bib, papers_dir=papers)

        assert result["partial_parse"] is True
        assert result["orphan_pdfs"] == []
        assert not any(i["type"] == "orphan_pdf" for i in result["issues"])


def test_validate_library_reports_orphans_normally_when_fully_parsed() -> None:
    """The guard above must not suppress orphan detection on a healthy library."""
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "ok.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        Path(os.path.join(papers, "loose.pdf")).write_bytes(b"%PDF-1.4\n")
        _write_bib(bib, '@article{smith2024, title = {A}}\n')

        result = validate_library(bib_path=bib, papers_dir=papers)

        assert result["partial_parse"] is False
        assert [Path(p).name for p in result["orphan_pdfs"]] == ["loose.pdf"]


def test_a_pdf_referenced_by_a_sibling_library_is_not_an_orphan() -> None:
    """The default layout gives every configured bib the same `papers_dir`.

    Checking one library therefore saw the *other* library's PDFs as unreferenced
    and `--fix` quarantined them, silently breaking that library's `file =`
    fields — for a user who ran `pzi fix clean` on the wrong target.
    """
    with tempfile.TemporaryDirectory() as td:
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        mine = os.path.join(papers, "mine2024.pdf")
        theirs = os.path.join(papers, "theirs2024.pdf")
        Path(mine).write_bytes(b"%PDF-1.4\nMINE\n")
        Path(theirs).write_bytes(b"%PDF-1.4\nTHEIRS\n")

        ml = os.path.join(td, "ml.bib")
        cs = os.path.join(td, "cs.bib")
        _write_bib(ml, f'@article{{mine2024, title = {{Mine}}, file = {{{mine}}}}}')
        _write_bib(cs, f'@article{{theirs2024, title = {{Theirs}}, file = {{{theirs}}}}}')

        result = validate_library(
            bib_path=ml, papers_dir=papers, sibling_bib_paths=[cs]
        )

        assert result["orphan_pdfs"] == []


def test_an_unreferenced_pdf_is_still_an_orphan_with_siblings_configured() -> None:
    with tempfile.TemporaryDirectory() as td:
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        stray = os.path.join(papers, "stray.pdf")
        Path(stray).write_bytes(b"%PDF-1.4\nSTRAY\n")

        ml = os.path.join(td, "ml.bib")
        cs = os.path.join(td, "cs.bib")
        _write_bib(ml, '@article{mine2024, title = {Mine}}')
        _write_bib(cs, '@article{theirs2024, title = {Theirs}}')

        result = validate_library(
            bib_path=ml, papers_dir=papers, sibling_bib_paths=[cs]
        )

        assert result["orphan_pdfs"] == [stray]


def test_an_unreadable_sibling_stops_orphan_detection_rather_than_guessing() -> None:
    """A sibling the parser could not read contributes no referenced paths.

    Same reasoning as the partial-parse guard on the target library: acting on an
    incomplete reference set is how a referenced PDF gets quarantined.
    """
    with tempfile.TemporaryDirectory() as td:
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        theirs = os.path.join(papers, "theirs2024.pdf")
        Path(theirs).write_bytes(b"%PDF-1.4\nTHEIRS\n")

        ml = os.path.join(td, "ml.bib")
        cs = os.path.join(td, "cs.bib")
        _write_bib(ml, '@article{mine2024, title = {Mine}}')
        _write_bib(cs, "@article{broken2024,\n  title = {Unclosed\n")

        result = validate_library(
            bib_path=ml, papers_dir=papers, sibling_bib_paths=[cs]
        )

        assert result["orphan_pdfs"] == []
        assert any("cs.bib" in message for message in result["errors"])


def test_fix_does_not_quarantine_while_any_reference_is_unresolved() -> None:
    """An unresolved `file =` means the referenced-path set is incomplete.

    The orphan sweep decides what to move by subtracting referenced paths from
    what is on disk, so a reference it could not resolve makes every genuine
    file look orphaned. It also covers the commoner case where a missing PDF
    means the file was *renamed* — in which case the loose file about to be
    quarantined may be the very one the entry wants.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "lib.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers)
        loose = os.path.join(papers, "loose.pdf")
        Path(loose).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            "@article{gone2020,\n"
            "  title = {Its PDF Was Renamed},\n"
            f"  file = {{{papers}/renamed-away.pdf}}\n"
            "}\n",
        )

        result = clean_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert os.path.exists(loose), "a file was quarantined on an incomplete reference set"
        assert result["actions"] == []
        assert any(issue["type"] == "quarantine_skipped" for issue in result["issues"])


def test_a_zotero_style_file_field_resolves_and_is_not_an_orphan() -> None:
    """`file = {desc:path:mime}` is one attachment, not a path.

    Read as a path it resolved to nothing, so the entry contributed no
    referenced path: its real PDF was reported as *missing* and as an *orphan*
    in the same run, and `--fix` acted on the second half. On an imported
    library that detached every attachment.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "lib.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers)
        pdf = os.path.join(papers, "zotero.pdf")
        Path(pdf).write_bytes(b"%PDF-1.4\n")
        _write_bib(
            bib,
            "@article{zoterostyle,\n"
            "  title = {Imported From Zotero},\n"
            f"  file = {{Full Text PDF:{pdf}:application/pdf}}\n"
            "}\n",
        )

        result = clean_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert result["missing_pdfs"] == []
        assert result["orphan_pdfs"] == []
        assert os.path.exists(pdf)
