"""Removing an entry files its PDF away, in the command that removed it.

Until 2026-09-01 `delete` and `library merge` dropped a BibTeX entry and left
its PDF on disk, referenced by nothing. Reclaiming it meant `library clean
--fix` — a whole-library sweep, run to finish a one-entry action, that would
also move unrelated orphans left over from any earlier incident. The comment in
`dedupe_service.py` said as much about merge: "a second command undoing what
this one caused."

Both commands now quarantine their own PDF, by the path they already resolved,
into the same `papers/.orphans/`. The file is moved, never deleted, so the pair
`.bak` + `.orphans/` still undoes the whole operation.

This file deliberately spans `delete` and `library merge` rather than sitting in
per-command modules: they share one helper, and the failure it exists to catch
is a fix landing in one of them and not the other.
"""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import api as pzi_api
from pzi import exit_codes
from pzi.commands.dedupe import run_merge_command
from pzi.commands.delete import run_delete_command


def _library(tmp_path: Path, bib_text: str) -> tuple[str, Path, Path]:
    papers = tmp_path / "papers"
    papers.mkdir()
    bib = tmp_path / "main.bib"
    bib.write_text(bib_text)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "t"\npath = "{bib}"\npapers_dir = "{papers}"\ndefault = true\n'
    )
    return str(config), bib, papers


def _entry_with_pdf(pdf: Path) -> str:
    return (
        "@article{keep2024,\n  title = {Keep Me},\n  year = {2024},\n}\n\n"
        f"@article{{drop2024,\n  title = {{Drop Me}},\n  year = {{2024}},\n  file = {{{pdf}}},\n}}\n"
    )


def _delete(config_path: str, tmp_path: Path, citekey, **overrides) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    args = Namespace(
        citekey=[citekey] if isinstance(citekey, str) else list(citekey),
        dry_run=False, force=True, json=False,
        target=None, config=config_path, keep_pdf=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    code = run_delete_command(
        args, home_dir=str(tmp_path), config_path=config_path,
        stdout=out, stderr=err, bib_selector=None,
    )
    return code, out.getvalue(), err.getvalue()


def test_delete_quarantines_the_entrys_pdf(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    code, out, _err = _delete(config, tmp_path, "drop2024")

    assert code == exit_codes.OK
    assert not pdf.exists()
    assert (papers / ".orphans" / "drop2024.pdf").read_text() == "pretend pdf"
    assert "PDF quarantined to" in out
    assert "would be quarantined" not in out
    assert "drop2024.pdf" in out


def test_a_pdf_a_surviving_entry_still_references_is_left_alone(tmp_path: Path) -> None:
    """The duplicate pair this feature exists to reconcile can share one file.

    Under the default `{citekey}.pdf` naming two entries never collide. But a
    content-derived `pdf_filename_format` — author-year-title, which is what a
    Zotero-style template renders — produces one filename for two duplicates,
    and `resolve_pdf_destination` returns the *existing* file when the bytes
    match rather than writing a second copy. Quarantining on citekey alone would
    then break the entry the user chose to keep.
    """
    config, bib, papers = _library(tmp_path, "")
    shared = papers / "shared.pdf"
    shared.write_text("one file, two entries")
    bib.write_text(
        f"@article{{keep2024,\n  title = {{Keep Me}},\n  file = {{{shared}}},\n}}\n\n"
        f"@article{{drop2024,\n  title = {{Drop Me}},\n  file = {{{shared}}},\n}}\n"
    )

    code, out, _err = _delete(config, tmp_path, "drop2024")

    assert code == exit_codes.OK
    assert shared.read_text() == "one file, two entries"
    assert not (papers / ".orphans").exists()
    assert "PDF left at" in out
    assert "still referenced by another entry" in out


def test_a_pdf_outside_the_papers_directory_is_left_where_it_is(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    elsewhere = tmp_path / "Downloads"
    elsewhere.mkdir()
    pdf = elsewhere / "drop2024.pdf"
    pdf.write_text("not pzi's to move")
    bib.write_text(_entry_with_pdf(pdf))

    code, out, _err = _delete(config, tmp_path, "drop2024")

    assert code == exit_codes.OK
    assert pdf.read_text() == "not pzi's to move"
    assert not (papers / ".orphans").exists()
    assert "PDF left at" in out
    assert "outside the library's papers directory" in out


def test_keep_pdf_leaves_the_file_and_still_deletes_the_entry(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    code, out, _err = _delete(config, tmp_path, "drop2024", keep_pdf=True)

    assert code == exit_codes.OK
    assert pdf.exists()
    assert "drop2024" not in bib.read_text()
    # Feature-sensitive: with the disposal deleted outright this line vanishes,
    # so the test cannot certify the old strand-the-file behaviour (audit A10).
    assert "PDF left at" in out


def test_a_dry_run_moves_nothing(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    code, out, _err = _delete(config, tmp_path, "drop2024", dry_run=True)

    assert code == exit_codes.OK
    assert pdf.exists()
    assert "drop2024" in bib.read_text()
    assert "DRY RUN" in out


def test_a_missing_pdf_is_not_an_error(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    bib.write_text(_entry_with_pdf(papers / "never-downloaded.pdf"))

    code, out, _err = _delete(config, tmp_path, "drop2024")

    assert code == exit_codes.OK
    assert "drop2024" not in bib.read_text()
    # Feature-sensitive (audit A10): the disposal names the outcome even when
    # there was nothing on disk to move.
    assert "not found on disk" in out


def _merge(config_path: str, tmp_path: Path, a: str, b: str, **overrides) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    args = Namespace(
        citekey_a=a, citekey_b=b, dry_run=False, json=False,
        target=None, config=config_path, keep_pdf=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    code = run_merge_command(
        args, home_dir=str(tmp_path), config_path=config_path,
        stdout=out, stderr=err, bib_selector=None,
    )
    return code, out.getvalue(), err.getvalue()


def test_merge_quarantines_the_dropped_entrys_pdf(tmp_path: Path) -> None:
    """The sibling half of the fix.

    `merge` computed `orphaned_pdf` and printed it long before it did anything
    about it; a fix that landed only in `delete` would leave the command the
    duplicate warning actually points at still stranding files.
    """
    config, bib, papers = _library(tmp_path, "")
    kept_pdf, dropped_pdf = papers / "keep2024.pdf", papers / "drop2024.pdf"
    kept_pdf.write_text("survivor")
    dropped_pdf.write_text("casualty")
    bib.write_text(
        f"@article{{keep2024,\n  title = {{A Paper}},\n  year = {{2024}},\n"
        f"  file = {{{kept_pdf}}},\n}}\n\n"
        f"@article{{drop2024,\n  title = {{A Paper}},\n  year = {{2024}},\n"
        f"  file = {{{dropped_pdf}}},\n}}\n"
    )

    code, out, _err = _merge(config, tmp_path, "drop2024", "keep2024")

    assert code == exit_codes.OK
    assert kept_pdf.read_text() == "survivor"
    assert not dropped_pdf.exists()
    assert (papers / ".orphans" / "drop2024.pdf").read_text() == "casualty"
    assert "quarantined" in out


def test_the_python_api_disposes_of_the_pdf_too(tmp_path: Path) -> None:
    """The facade is the third surface, and the one that documented the old rule.

    `api.delete`'s docstring stated "The entry's PDF is left on disk" as a
    deliberate contract. Leaving it behind while the CLI moved the file would
    make `pzi.delete(...)` and `pzi delete` two different operations.
    """
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    report = pzi_api.delete("drop2024", dry_run=False, config_path=config)

    assert not pdf.exists()
    assert (papers / ".orphans" / "drop2024.pdf").read_text() == "pretend pdf"
    assert report["pdf_action"]["status"] == "moved"


def test_the_python_api_honours_keep_pdf(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    report = pzi_api.delete("drop2024", dry_run=False, keep_pdf=True, config_path=config)

    assert "pdf_action" not in report
    assert pdf.exists()


# ── several citekeys at once ─────────────────────────────────────────────────


def _three_entries(papers: Path) -> str:
    text = ""
    for key in ("keep2024", "dropa2024", "dropb2024"):
        pdf = papers / f"{key}.pdf"
        pdf.write_text(f"pdf for {key}")
        text += f"@article{{{key},\n  title = {{{key}}},\n  file = {{{pdf}}},\n}}\n\n"
    return text


def test_delete_takes_several_citekeys_in_one_call(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    bib.write_text(_three_entries(papers))

    code, _out, _err = _delete(config, tmp_path, ["dropa2024", "dropb2024"])

    assert code == exit_codes.OK
    remaining = bib.read_text()
    assert "keep2024" in remaining
    assert "dropa2024" not in remaining
    assert "dropb2024" not in remaining
    assert (papers / "keep2024.pdf").exists()
    assert (papers / ".orphans" / "dropa2024.pdf").exists()
    assert (papers / ".orphans" / "dropb2024.pdf").exists()


def test_a_batch_where_only_some_citekeys_exist_is_partial(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    bib.write_text(_three_entries(papers))

    code, _out, err = _delete(config, tmp_path, ["dropa2024", "nosuch2024"])

    assert code == exit_codes.PARTIAL
    assert "dropa2024" not in bib.read_text()
    assert "nosuch2024" in err


def test_a_batch_where_no_citekey_exists_is_not_found(tmp_path: Path) -> None:
    config, bib, papers = _library(tmp_path, "")
    bib.write_text(_three_entries(papers))

    code, _out, _err = _delete(config, tmp_path, ["nosuch2024", "alsonot2024"])

    assert code == exit_codes.NOT_FOUND
    assert "dropa2024" in bib.read_text()


def test_a_dry_run_previews_the_quarantine_rather_than_its_own_reference(
    tmp_path: Path,
) -> None:
    """A preview runs *before* the entry is removed, so it is still in the bib.

    Reading the reference set naively then finds the entry's own `file =` field
    and reports "still referenced by another entry" — the preview contradicting
    what the real run does, which is the one thing a preview must not do.
    """
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    code, out, _err = _delete(config, tmp_path, "drop2024", dry_run=True)

    assert code == exit_codes.OK
    assert pdf.exists()
    # Tense matters: a preview that says "quarantined" reports a move it has
    # not made. `merge`'s preview line is built from the same clause.
    assert "PDF would be quarantined to" in out
    assert "still referenced" not in out


# ── audit findings 2026-09-01 (notes/review-audit-0.2.0.html, section A) ─────


def test_an_unreadable_sibling_vetoes_the_quarantine(tmp_path: Path) -> None:
    """A1: 'no references' and 'references we could not see' are not the same.

    `referenced_pdf_paths` reports unreadable blocks precisely because only one
    of those is safe to quarantine against; discarding the errors moved a PDF a
    sibling's intact-on-disk `file =` still pointed at.
    """
    papers = tmp_path / "papers"
    papers.mkdir()
    pdf = papers / "drop2024.pdf"
    pdf.write_text("shared with a sibling")
    bib_a = tmp_path / "a.bib"
    bib_a.write_text(f"@article{{drop2024,\n  title = {{Mine}},\n  file = {{{pdf}}},\n}}\n")
    bib_b = tmp_path / "b.bib"
    bib_b.write_text(f"@article{{broken,\n  title = {{Broken {{\n  file = {{{pdf}}},\n}}\n")
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "a"\npath = "{bib_a}"\npapers_dir = "{papers}"\ndefault = true\n'
        f'[[bibs]]\nname = "b"\npath = "{bib_b}"\npapers_dir = "{papers}"\n'
    )

    code, out, _err = _delete(str(config), tmp_path, "drop2024")

    assert code == exit_codes.OK
    assert pdf.exists(), "PDF moved despite an unreadable sibling reference set"
    assert not (papers / ".orphans").exists()
    assert "could not verify references" in out


def test_a_batch_dry_run_previews_the_quarantine_of_a_shared_pdf(tmp_path: Path) -> None:
    """A2: deleting both entries that share a PDF must preview the move.

    The exclusion carried only the current entry's citekey, so each preview saw
    the *other* doomed entry still referencing the file and reported 'left —
    still referenced' for a file the real run quarantines.
    """
    config, bib, papers = _library(tmp_path, "")
    shared = papers / "shared.pdf"
    shared.write_text("one file, two doomed entries")
    bib.write_text(
        f"@article{{dropa2024,\n  title = {{A}},\n  file = {{{shared}}},\n}}\n\n"
        f"@article{{dropb2024,\n  title = {{B}},\n  file = {{{shared}}},\n}}\n"
    )

    code, out, _err = _delete(config, tmp_path, ["dropa2024", "dropb2024"], dry_run=True)

    assert code == exit_codes.OK
    assert shared.exists()
    assert "would be quarantined" in out
    assert "still referenced" not in out


def test_the_api_preview_action_declares_itself_a_preview(tmp_path: Path) -> None:
    """A5: `pzi.delete()` defaults to dry_run=True and hands over the raw action.

    The action said `{"status": "moved", "destination": ...}` for a destination
    that does not exist; the CLI compensated with tense, the Python surface did
    not. The action now carries `dry_run` itself, so a consumer inspecting
    `pdf_action` alone can tell preview from fact.
    """
    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    report = pzi_api.delete("drop2024", config_path=config)  # preview by default

    assert report["pdf_action"]["status"] == "moved"
    assert report["pdf_action"]["dry_run"] is True
    assert pdf.exists()

    applied = pzi_api.delete("drop2024", dry_run=False, config_path=config)
    assert applied["pdf_action"]["status"] == "moved"
    assert "dry_run" not in applied["pdf_action"]


def test_merge_names_a_pdf_that_was_never_on_disk(tmp_path: Path) -> None:
    """A7: the merge line ended `..., ` — trailing comma, empty clause."""
    config, bib, papers = _library(tmp_path, "")
    kept = papers / "keep2024.pdf"
    kept.write_text("survivor")
    bib.write_text(
        f"@article{{keep2024,\n  title = {{A Paper}},\n  file = {{{kept}}},\n}}\n\n"
        f"@article{{drop2024,\n  title = {{A Paper}},\n"
        f"  file = {{{papers / 'never-downloaded.pdf'}}},\n}}\n"
    )

    code, out, _err = _merge(config, tmp_path, "drop2024", "keep2024")

    assert code == exit_codes.OK
    lines = [line for line in out.splitlines() if "PDF orphaned" in line]
    assert lines, out
    assert not lines[0].rstrip().endswith(","), f"dangling clause: {lines[0]!r}"
    assert "not found on disk" in lines[0]


def test_a_failed_move_is_a_finding_on_every_surface(tmp_path: Path) -> None:
    """A3 + the mkdir hole: delete's text path exited 1 on a failed quarantine,
    merge's --json path exited 0 for the identical failure — and an unwritable
    papers dir crashed `quarantine_pdf` outright (`mkdir` outside the try).

    One test spans both commands: the failure mode this suite exists to catch is
    a fix landing in one of them and not the other.
    """
    import os as _os

    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    keep_pdf_file = papers / "keep2024.pdf"
    keep_pdf_file.write_text("survivor")
    bib.write_text(
        f"@article{{keep2024,\n  title = {{A Paper}},\n  file = {{{keep_pdf_file}}},\n}}\n\n"
        f"@article{{drop2024,\n  title = {{A Paper}},\n  file = {{{pdf}}},\n}}\n"
    )
    _os.chmod(papers, 0o555)  # .orphans/ cannot be created
    try:
        code, _out, err = _merge(config, tmp_path, "drop2024", "keep2024", json=True)
    finally:
        _os.chmod(papers, 0o755)

    assert code == exit_codes.FINDINGS, "merge --json must report the failed move"
    assert pdf.exists()

    # Same failure through delete, JSON path, for the sibling guarantee.
    bib2 = tmp_path / "second.bib"
    pdf2 = papers / "solo2024.pdf"
    pdf2.write_text("second")
    bib2.write_text(f"@article{{solo2024,\n  title = {{B}},\n  file = {{{pdf2}}},\n}}\n")
    config2 = tmp_path / "config2.toml"
    config2.write_text(
        f'[[bibs]]\nname = "t2"\npath = "{bib2}"\npapers_dir = "{papers}"\ndefault = true\n'
    )
    _os.chmod(papers, 0o555)
    try:
        code2, _out2, _err2 = _delete(str(config2), tmp_path, "solo2024", json=True)
    finally:
        _os.chmod(papers, 0o755)
    assert code2 == exit_codes.FINDINGS


def test_repeating_a_citekey_in_one_call_is_one_delete(tmp_path: Path) -> None:
    """A6: `pzi delete a a` previewed two successes, then the real run deleted
    once and exited 4 claiming "1 not found" — for an entry it had just deleted.
    """
    config, bib, papers = _library(tmp_path, "")
    bib.write_text(_three_entries(papers))

    code_preview, out_preview, _ = _delete(
        config, tmp_path, ["dropa2024", "dropa2024"], dry_run=True
    )
    assert code_preview == exit_codes.OK
    assert out_preview.count("would delete") == 1

    code_real, out_real, _ = _delete(config, tmp_path, ["dropa2024", "dropa2024"])
    assert code_real == exit_codes.OK
    assert out_real.count("deleted:") == 1


def test_cancelling_the_prompt_reports_a_zero_count(tmp_path: Path, monkeypatch) -> None:
    """A8: the cancel envelope said `deleted: false` where every other path
    reports an int; `false` and `0` are different values to a typed consumer.
    """
    import io
    import sys as _sys

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    config, bib, papers = _library(tmp_path, "")
    bib.write_text(_three_entries(papers))
    monkeypatch.setattr(_sys, "stdin", _Tty("n\n"))

    out, err = StringIO(), StringIO()
    args = Namespace(
        citekey=["dropa2024"], dry_run=False, force=False, json=True,
        target=None, config=config, keep_pdf=False,
    )
    code = run_delete_command(
        args, home_dir=str(tmp_path), config_path=config,
        stdout=out, stderr=err, bib_selector=None,
    )
    import json as _json

    assert code == exit_codes.OK
    envelope = _json.loads(out.getvalue())
    assert envelope["deleted"] == 0
    assert isinstance(envelope["deleted"], int) and not isinstance(envelope["deleted"], bool)
    assert "dropa2024" in bib.read_text()


def test_http_delete_disposes_of_the_pdf_like_the_other_surfaces(tmp_path: Path) -> None:
    """A4 (decision: full parity): `POST /delete` is the fourth surface.

    It called `delete_entry` directly and stranded the PDF while CLI and facade
    quarantined — the audit's one *deliberate* new sibling divergence. It now
    runs the same disposal and honours a `keep_pdf` body flag.
    """
    from pzi import http_post_routes

    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    status, body = http_post_routes.process_post_request(
        "/delete",
        {"citekey": "drop2024", "force": True},
        config,
        str(tmp_path),
    )

    assert status == 200
    assert not pdf.exists()
    assert (papers / ".orphans" / "drop2024.pdf").read_text() == "pretend pdf"
    assert body["pdf_action"]["status"] == "moved"


def test_http_delete_honours_keep_pdf(tmp_path: Path) -> None:
    from pzi import http_post_routes

    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    status, body = http_post_routes.process_post_request(
        "/delete",
        {"citekey": "drop2024", "force": True, "keep_pdf": True},
        config,
        str(tmp_path),
    )

    assert status == 200
    assert pdf.exists()
    assert "pdf_action" not in body


def test_http_delete_preview_plans_but_does_not_move(tmp_path: Path) -> None:
    from pzi import http_post_routes

    config, bib, papers = _library(tmp_path, "")
    pdf = papers / "drop2024.pdf"
    pdf.write_text("pretend pdf")
    bib.write_text(_entry_with_pdf(pdf))

    status, body = http_post_routes.process_post_request(
        "/delete", {"citekey": "drop2024"}, config, str(tmp_path)
    )

    assert status == 200
    assert pdf.exists()
    assert body["pdf_action"]["status"] == "moved"
    assert body["pdf_action"]["dry_run"] is True
