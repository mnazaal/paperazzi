"""Tests for pzi.reindex_service — citekey regeneration."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

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


def test_reindex_repoints_a_shared_pdf_instead_of_leaving_it_dangling() -> None:
    """Two entries referencing one PDF: the second must not be left pointing at
    a path the first rename already moved away."""
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "shared.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        shared = os.path.join(papers, "shared.pdf")
        Path(shared).write_bytes(b"%PDF-1.4\nSHARED\n")
        _write_bib(
            bib,
            f'@article{{k1, title = {{First Paper}}, author = {{Smith, Jane}},'
            f' year = {{2020}}, file = {{{shared}}}}}\n'
            f'@article{{k2, title = {{Second Paper}}, author = {{Jones, Ann}},'
            f' year = {{2021}}, file = {{{shared}}}}}',
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        written = Path(bib).read_text()
        referenced = [
            line.split("{", 1)[1].rstrip("},")
            for line in written.splitlines()
            if line.strip().startswith("file = ")
        ]
        assert referenced, written
        for path in referenced:
            assert Path(path).exists(), f"dangling file reference: {path}"
        # And the conflict is reported rather than silently swallowed.
        assert result["errors"]


def test_reindex_leaves_a_pdf_outside_papers_dir_where_it_is() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "external.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        elsewhere = os.path.join(td, "Documents")
        os.makedirs(elsewhere, exist_ok=True)
        external = os.path.join(elsewhere, "my-important-paper.pdf")
        Path(external).write_bytes(b"%PDF-1.4\nEXTERNAL\n")
        _write_bib(
            bib,
            f'@article{{oldkey, title = {{New Test}}, author = {{Doe, John}},'
            f' year = {{2025}}, file = {{{external}}}}}',
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert result["changed"][0]["new_citekey"] == "doe2025new"
        assert Path(external).exists()
        assert list(Path(papers).glob("*.pdf")) == []
        assert external in Path(bib).read_text()


def test_reindex_dry_run_matches_what_the_real_run_will_do() -> None:
    """The real run refuses to overwrite an occupied destination; the preview
    used to promise the rename anyway."""
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "occupied.bib")
        papers = os.path.join(td, "papers")
        os.makedirs(papers, exist_ok=True)
        real_pdf = os.path.join(papers, "real-paper.pdf")
        Path(real_pdf).write_bytes(b"%PDF-1.4\nREAL\n")
        Path(os.path.join(papers, "doe2025new.pdf")).write_bytes(b"%PDF-1.4\nOTHER\n")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},'
            f' file = {{{real_pdf}}}}}',
        )

        preview = reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)
        real = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert preview["changed"][0]["renamed_pdf"] is False
        assert real["changed"][0]["renamed_pdf"] is False
        assert preview["errors"] and real["errors"]


def test_reindex_writes_a_backup_before_rewriting_the_library() -> None:
    """`--rename-citekeys` rewrites every entry key and has no undo otherwise.

    `delete` and `library merge` — the other two commands that destroy something a
    user cannot reconstruct — both leave a `.bak` under the lock. This one
    rewrote the whole library with none.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "change.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025}}',
        )
        before = Path(bib).read_text()

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        backup = result["backup_path"]
        assert backup is not None
        assert Path(backup).read_text() == before
        assert "oldkey" not in Path(bib).read_text()


def test_reindex_dry_run_writes_no_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "change.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            '@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025}}',
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)

        assert result["backup_path"] is None
        assert list(Path(td).glob("*.bak")) == []


def test_reindex_with_nothing_to_change_writes_no_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "clean.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            '@article{smith2024test, title = {Test}, author = {Smith}, year = {2024}}',
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        assert result["backup_path"] is None
        assert list(Path(td).glob("*.bak")) == []


def test_a_renamed_citekey_is_never_reissued_to_another_entry() -> None:
    """A key an entry vacates must stay reserved for the rest of the run.

    Freeing it means a later entry can be assigned the key an earlier one just
    gave up. Every other citekey break is loud — `\\cite{}` stops resolving and
    LaTeX says so — but this one silently leaves the citation resolving to a
    *different paper*, which no build error will ever surface.

    Here `smith2020study` is held by a paper by Zulu, and the paper that would
    naturally be keyed `smith2020study` sits under `alpha2020`. Reindexing
    renames the first, and must not then hand its old key to the second.
    """
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "lib.bib")
        _write_bib(
            bib,
            "@article{smith2020study,\n"
            "  author = {Zulu, Alan},\n"
            "  title = {Completely Different Paper},\n"
            "  year = {1999}\n"
            "}\n\n"
            "@article{alpha2020,\n"
            "  author = {Smith, Jane},\n"
            "  title = {A Study of Widgets},\n"
            "  year = {2020}\n"
            "}\n",
        )

        result = reindex_library(
            bib_path=bib,
            papers_dir=os.path.join(td, "papers"),
            dry_run=True,
        )

        new_keys = [c["new_citekey"] for c in result["changed"]]
        old_keys = {c["old_citekey"] for c in result["changed"]}
        assert not (set(new_keys) & old_keys), (
            f"a vacated citekey was reissued: renames were {result['changed']}"
        )
        assert len(new_keys) == len(set(new_keys)), "two entries were given the same citekey"


def test_a_dry_run_refuses_what_the_real_run_refuses() -> None:
    """The preview ran a weaker set of gates than the write it previews.

    A library the real run declines outright — exit 5, nothing written —
    previewed as a feasible list of renames at exit 1, and one of the renames it
    offered was for the very entry that causes the refusal. A preview is where
    the user decides to proceed, so previewing a different command is worse than
    not previewing at all.
    """
    import pytest

    from pzi.errors import PziError

    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "wedged.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            "@article{aaa,\n  title = {One},\n  author = {Smith, Jane},\n"
            "  year = {2020},\n}\n"
            "@article{,\n  title = {Keyless},\n  year = {2021},\n}\n",
        )

        with pytest.raises(PziError) as preview_error:
            reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)
        with pytest.raises(PziError) as applied_error:
            reindex_library(bib_path=bib, papers_dir=papers, dry_run=False)

        # Same refusal, same words, so the exit code is the same too.
        assert preview_error.value.message == applied_error.value.message
        assert "no citekey" in preview_error.value.message


def test_a_dry_run_still_previews_a_healthy_library() -> None:
    """The added gates must not refuse anything the real run accepts."""
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "fine.bib")
        papers = os.path.join(td, "papers")
        _write_bib(
            bib,
            "@article{oldkey,\n  title = {A Paper},\n  author = {Smith, Jane},\n"
            "  year = {2020},\n}\n",
        )

        result = reindex_library(bib_path=bib, papers_dir=papers, dry_run=True)

        assert result["status"] == "ok"
        assert [c["new_citekey"] for c in result["changed"]] == ["smith2020paper"]


# ── audit C1/C2: backup ordering, and shared-PDF preview parity (2026-09-02) ─


def test_the_backup_exists_before_any_pdf_is_renamed(tmp_path, monkeypatch) -> None:
    """C1: PDFs were renamed *before* the `.bak` was taken.

    A failed `mkdir`/`copy2` — disk full, permissions — therefore propagated
    with every PDF already renamed, the bib untouched, no backup, and no
    message saying where the files went. The undo block guarded only the bib
    rewrite. Ordering is the fix: nothing on disk moves until the undo artifact
    exists.
    """
    from pzi import reindex_service

    seen: dict[str, bool] = {"backup_before_rename": False}
    real_rename = reindex_service._rename_planned_pdfs

    def _spy(*args, **kwargs):
        seen["backup_before_rename"] = any(
            p.name.endswith(".reindex.bak") for p in tmp_path.rglob("*.bak")
        )
        return real_rename(*args, **kwargs)

    monkeypatch.setattr(reindex_service, "_rename_planned_pdfs", _spy)

    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "old2020.pdf").write_bytes(b"%PDF-1.4\n")
    bib = tmp_path / "t.bib"
    bib.write_text(
        "@article{old2020,\n  title = {A Paper},\n  author = {Smith, Jane},\n"
        f"  year = {{2020}},\n  file = {{{papers / 'old2020.pdf'}}},\n}}\n"
    )

    reindex_service.reindex_library(
        bib_path=str(bib), papers_dir=str(papers),
        citekey_format='auth.lower + "-" + year', dry_run=False,
    )

    assert seen["backup_before_rename"], "PDFs were renamed before the backup existed"


def test_the_preview_of_two_entries_sharing_one_pdf_matches_the_run(tmp_path) -> None:
    """C2: the preview promised two clean renames where the run does one.

    With both entries pointing at the same file, the real run renames it once
    and repoints the second entry, reporting an error for it. The preview
    marked both `renamed_pdf: True` with no errors, because its
    already-planned check looked only at colliding *destinations*, never at a
    shared *source*.
    """
    from pzi import reindex_service

    papers = tmp_path / "papers"
    papers.mkdir()
    shared = papers / "shared.pdf"
    shared.write_bytes(b"%PDF-1.4\n")
    bib = tmp_path / "t.bib"
    bib.write_text(
        f"@article{{aa2020,\n  author = {{Alpha, A}},\n  year = {{2020}},\n"
        f"  file = {{{shared}}},\n}}\n\n"
        f"@article{{bb2021,\n  author = {{Beta, B}},\n  year = {{2021}},\n"
        f"  file = {{{shared}}},\n}}\n"
    )
    common = dict(
        bib_path=str(bib), papers_dir=str(papers),
        citekey_format='auth.lower + "-" + year',
    )

    preview = reindex_service.reindex_library(**common, dry_run=True)
    previewed = sum(1 for c in preview["changed"] if c.get("renamed_pdf"))

    assert previewed == 1, (
        "preview claims two renames of one file; the run performs one "
        f"(changed: {preview['changed']})"
    )
    assert any("shares its PDF" in e for e in preview["errors"]), preview["errors"]


# ── --rename-files: resync filenames to pdf_filename_format ─────────────────


def _library_with_names(tmp_path, entries):
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    text = ""
    for citekey, title, filename in entries:
        (papers / filename).write_bytes(b"%PDF-1.4\n")
        text += (
            f"@article{{{citekey},\n  title = {{{title}}},\n"
            f"  file = {{{papers / filename}}},\n}}\n\n"
        )
    bib = tmp_path / "t.bib"
    bib.write_text(text)
    return str(bib), str(papers)


def test_rename_files_is_scoped_to_residue_by_default(tmp_path) -> None:
    """The reason this is not simply "rename every mismatch".

    On the real 23k library, 10,695 attached files differ from what the
    template produces — Better BibTeX named them from Zotero's sentence-case
    title while pzi renders the exported one. Renaming those is a mass rewrite
    nobody asked for. The default targets only names carrying LaTeX command
    residue, which current pzi could never emit.
    """
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("mangled2024", "A Real Title", "A textbraceleft Mangled Name.pdf"),
        ("cosmetic2024", "The Complexity of Things", "the complexity of things.pdf"),
    ])

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=True,
    )

    planned = {c["citekey"] for c in result["changed"]}
    assert planned == {"mangled2024"}, result["changed"]
    assert result["skipped_cosmetic"] == 1


def test_rename_files_all_includes_cosmetic_mismatches(tmp_path) -> None:
    """`--all` is the deliberate opt-in for a template change."""
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("mangled2024", "A Real Title", "A textbraceleft Mangled Name.pdf"),
        ("cosmetic2024", "The Complexity of Things", "the complexity of things.pdf"),
    ])

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=True, include_all=True,
    )

    assert {c["citekey"] for c in result["changed"]} == {"mangled2024", "cosmetic2024"}


def test_rename_files_applies_and_repoints_the_entry(tmp_path) -> None:
    from pzi import reindex_service
    from pzi.bib_repository import read_bib_file

    bib, papers = _library_with_names(tmp_path, [
        ("mangled2024", "A Real Title", "A textbraceleft Mangled Name.pdf"),
    ])

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=False,
    )

    assert result["status"] == "ok", result
    assert (Path(papers) / "A Real Title.pdf").exists()
    assert not (Path(papers) / "A textbraceleft Mangled Name.pdf").exists()
    stored = read_bib_file(bib)["records"][0]["local_pdf_path"]
    assert Path(stored).name == "A Real Title.pdf"
    # Same undo artifact as the citekey path.
    assert result["backup_path"]


def test_rename_files_refuses_to_overwrite_a_different_file(tmp_path) -> None:
    """The refusal the same-file guard below must not weaken."""
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("mangled2024", "A Real Title", "A textbraceleft Mangled Name.pdf"),
    ])
    (Path(papers) / "A Real Title.pdf").write_bytes(b"%PDF-1.4\nOTHER\n")

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=True,
    )

    assert result["changed"] == []
    assert any("already exists" in e for e in result["errors"]), result["errors"]


def test_rename_files_destination_that_is_the_source_is_not_a_collision(
    tmp_path,
) -> None:
    """A destination resolving to the source file itself does not block it.

    On a case-insensitive filesystem — macOS by default — the target of a
    case-only rename already "exists": it *is* the source. A bare existence
    test therefore refused every such rename on macOS while performing it on
    Linux. A hardlink reproduces the same condition on either platform.
    """
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("mangled2024", "A Real Title", "A textbraceleft Mangled Name.pdf"),
    ])
    os.link(
        Path(papers) / "A textbraceleft Mangled Name.pdf",
        Path(papers) / "A Real Title.pdf",
    )

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=True,
    )

    assert result["errors"] == []
    assert [c["citekey"] for c in result["changed"]] == ["mangled2024"]


def test_reindex_destination_that_is_the_source_is_not_a_collision(tmp_path) -> None:
    """The citekey pass makes the same judgement, in preview and in the run.

    Same condition as the filename pass above, reached through the other two
    call sites: the preview's planning loop and `_rename_planned_pdfs`.
    """
    from pzi import reindex_service
    from pzi.bib_repository import read_bib_file

    papers = tmp_path / "papers"
    papers.mkdir()
    source = papers / "real-paper.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    os.link(source, papers / "doe2025new.pdf")
    bib = tmp_path / "t.bib"
    bib.write_text(
        "@article{oldkey, title = {New Test}, author = {Doe, John}, year = {2025},"
        f" file = {{{source}}}}}"
    )
    common = dict(bib_path=str(bib), papers_dir=str(papers))

    preview = reindex_service.reindex_library(**common, dry_run=True)
    real = reindex_service.reindex_library(**common, dry_run=False)

    assert preview["errors"] == [], preview["errors"]
    assert real["errors"] == [], real["errors"]
    assert preview["changed"][0]["renamed_pdf"] is True
    assert real["changed"][0]["renamed_pdf"] is True
    assert (papers / "doe2025new.pdf").exists()
    stored = read_bib_file(str(bib))["records"][0]["local_pdf_path"]
    assert Path(stored).name == "doe2025new.pdf"


def _case_insensitive_path_ops():
    """`os.path.exists`/`samefile` as a case-insensitive filesystem answers them.

    macOS's default filesystem folds case; Linux's does not, so the condition
    that loses a PDF there cannot be staged here directly. Patching the two
    lookups the rename guard consults reproduces the decision the guard has to
    make, which is the part under test.
    """
    real_exists, real_samefile = os.path.exists, os.path.samefile

    def fold(p):
        p = os.fspath(p)
        if real_exists(p):
            return p
        directory, name = os.path.split(p)
        if not real_exists(directory):
            return p
        for entry in os.listdir(directory):
            if entry.lower() == name.lower():
                return os.path.join(directory, entry)
        return p

    return (
        lambda p: real_exists(fold(p)),
        lambda a, b: real_samefile(fold(a), fold(b)),
    )


def test_rename_files_does_not_clobber_a_sibling_renamed_earlier_in_the_batch(
    tmp_path, monkeypatch,
) -> None:
    """Two targets differing only in case are one file on macOS.

    Both passed the planning check, which compares destination strings and runs
    before anything has moved; the second `os.rename` then replaced the PDF the
    first had just written. The guard has to be asked again per move.
    """
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("one2024", "Alpha Beta", "one textbraceleft mangled.pdf"),
        ("two2024", "alpha beta", "two textbraceleft mangled.pdf"),
    ])
    ci_exists, ci_samefile = _case_insensitive_path_ops()
    monkeypatch.setattr(os.path, "exists", ci_exists)
    monkeypatch.setattr(os.path, "samefile", ci_samefile)

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=False,
    )

    assert len(result["changed"]) == 1, result["changed"]
    assert any("already exists" in e for e in result["errors"]), result["errors"]
    # Neither PDF was destroyed: one moved, the other stayed where it was.
    survivors = sorted(p.name for p in Path(papers).glob("*.pdf"))
    assert len(survivors) == 2, survivors


def test_rename_files_preview_matches_the_run_where_case_folds(
    tmp_path, monkeypatch,
) -> None:
    """The dry run and the real run must plan the same thing on macOS.

    `planned_destinations` compared destination *strings*, so two targets
    differing only in case read as two names where the filesystem has one
    directory entry: the preview offered two renames for what the run performs
    as one rename and one refusal. No PDF was ever at risk — the apply-time
    guard catches it — but a preview of a different command is the property
    `test_reindex_dry_run_matches_what_the_real_run_will_do` exists to hold.
    """
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("one2024", "Alpha Beta", "one textbraceleft mangled.pdf"),
        ("two2024", "alpha beta", "two textbraceleft mangled.pdf"),
    ])
    ci_exists, ci_samefile = _case_insensitive_path_ops()
    monkeypatch.setattr(os.path, "exists", ci_exists)
    monkeypatch.setattr(os.path, "samefile", ci_samefile)

    preview = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=True,
    )
    run = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=False,
    )

    assert len(preview["changed"]) == 1, preview["changed"]
    assert [c["new_pdf"] for c in preview["changed"]] == [
        c["new_pdf"] for c in run["changed"]
    ]
    assert len(preview["errors"]) == len(run["errors"]) == 1
    assert "already exists" in preview["errors"][0]


def test_rename_files_renames_both_where_case_does_not_fold(tmp_path) -> None:
    """The same two targets are two files on Linux, and both must rename.

    This is why the planned-destination set is not simply case-folded: folding
    unconditionally turns a cosmetic divergence on a machine the user does not
    have into a real refusal on the one they work at. The probe is what tells
    the two filesystems apart, and this test fails if it is removed.
    """
    from pzi import reindex_service
    from pzi.fileio import directory_folds_case

    bib, papers = _library_with_names(tmp_path, [
        ("one2024", "Alpha Beta", "one textbraceleft mangled.pdf"),
        ("two2024", "alpha beta", "two textbraceleft mangled.pdf"),
    ])
    if directory_folds_case(papers):
        pytest.skip("this filesystem folds case; Linux CI covers the other branch")

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=False,
    )

    assert len(result["changed"]) == 2, result["changed"]
    assert result["errors"] == [], result["errors"]
    assert sorted(p.name for p in Path(papers).glob("*.pdf")) == [
        "Alpha Beta.pdf", "alpha beta.pdf",
    ]


def test_rename_files_repoints_the_second_entry_sharing_one_file(tmp_path) -> None:
    """Two entries can name one file without naming it the same way.

    A hard link states it portably; on a case-folding filesystem, two spellings
    of one name are the same thing. Either way the planned-source set saw two
    distinct path strings and planned two renames of one file — here that ends
    with the library holding two names for one inode, and on a folding
    filesystem with the second `os.rename` finding a source that has already
    moved. The rule the run applies is rename once, repoint the rest, so the
    set has to compare identity rather than spelling.
    """
    from pzi import reindex_service

    bib, papers = _library_with_names(tmp_path, [
        ("one2024", "Alpha", "one textbraceleft mangled.pdf"),
        ("two2024", "Beta", "two textbraceleft mangled.pdf"),
    ])
    shared = Path(papers) / "one textbraceleft mangled.pdf"
    linked = Path(papers) / "two textbraceleft mangled.pdf"
    linked.unlink()
    os.link(shared, linked)

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers,
        pdf_filename_format="{{ title }}", dry_run=False,
    )

    assert len(result["changed"]) == 1, result["changed"]
    assert any("shares its PDF" in e for e in result["errors"]), result["errors"]
