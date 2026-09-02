"""Citekey regeneration and file-reference repair."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from pzi.bib_repository import (
    backup_path_for,
    parse_bib_library,
    read_bib_file_raw_with_failures,
    read_bib_notices,
    read_bib_source,
    rewrite_entries_in_order_locked,
    validate_bibtex_roundtrip,
    validate_library_parseable,
    with_bib_lock,
)
from pzi.bibtex import BibtexEntry, NormalizedRecord
from pzi.format_templates import format_citekey, format_pdf_filename
from pzi.pdf_planning import plan_pdf_path


class CitekeyChange(TypedDict):
    """One planned citekey rename, with the PDF move it implies."""

    entry_index: int
    old_citekey: str
    new_citekey: str
    renamed_pdf: bool
    old_pdf: NotRequired[str]
    new_pdf: NotRequired[str]
    #: A stored PDF outside ``papers_dir``, left exactly where it is.
    kept_external_pdf: NotRequired[str]


def _is_under(path: str, directory: str) -> bool:
    """Is *path* inside *directory*? Compared without touching the filesystem."""
    try:
        Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(directory)))
    except ValueError:
        return False
    return True


class ReindexResult(TypedDict):
    status: str
    bib_path: str
    total_entries: int
    changed: list[dict[str, Any]]
    errors: list[str]
    #: Read notices that do not stop the audit but change what its counts mean:
    #: a missing bib, and blocks the lenient parse dropped.
    warnings: NotRequired[list[str]]
    #: Where the pre-rewrite library was copied, or ``None`` when nothing was
    #: rewritten (a dry run, or a library already matching ``citekey_format``).
    backup_path: NotRequired[str | None]
    #: Attached files whose name differs from the template only in case and
    #: punctuation, which ``--rename-files`` leaves alone without ``--all``.
    #: Only the filename pass sets it; the citekey pass does not.
    skipped_cosmetic: NotRequired[int]


def _entries_with_planned_citekeys(
    entries: list[BibtexEntry], changes: list[CitekeyChange]
) -> list[BibtexEntry]:
    """*entries* as the apply path would leave them, without touching them.

    The apply path renames in place inside ``_rename_planned_pdfs``, so a dry
    run had no renamed entry list to hand to the write's own validation gate —
    which is why it could not run it.
    """
    previewed = [dict(entry) for entry in entries]
    for change in changes:
        previewed[change["entry_index"]]["citekey"] = change["new_citekey"]
    return cast("list[BibtexEntry]", previewed)


def plan_reindex(
    *,
    entries: list[BibtexEntry],
    records: list[NormalizedRecord],
    papers_dir: str,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
) -> list[CitekeyChange]:
    """Decide every citekey rename and the PDF move each one implies.

    Pure: touches neither disk nor the entries passed in.  The PDF to move comes
    from the record's own ``local_pdf_path`` — never from guessing a filename
    out of the old citekey, which would pick up an unrelated namesake file and
    orphan the entry's real PDF.
    """
    existing_keys: set[str] = {entry["citekey"] for entry in entries}
    changes: list[CitekeyChange] = []

    for index, entry in enumerate(entries):
        old_citekey = entry["citekey"]
        record = records[index] if index < len(records) else {}

        new_citekey = format_citekey(citekey_format, record, existing_keys - {old_citekey})
        if new_citekey == old_citekey:
            continue

        # Reserve the vacated key for the rest of the run rather than freeing
        # it. Discarding it let a later entry be assigned the key an earlier one
        # had just given up, so `\cite{smith2020study}` in an existing .tex kept
        # resolving — to a different paper. Every other citekey break is loud
        # (LaTeX reports an undefined reference); this one is silent and wrong.
        existing_keys.add(new_citekey)

        change: CitekeyChange = {
            "entry_index": index,
            "old_citekey": old_citekey,
            "new_citekey": new_citekey,
            "renamed_pdf": False,
        }

        stored_pdf = record.get("local_pdf_path")
        if isinstance(stored_pdf, str) and stored_pdf.strip():
            old_pdf = str(Path(stored_pdf).expanduser())
            if _is_under(old_pdf, papers_dir):
                new_pdf = plan_pdf_path(
                    papers_dir=papers_dir,
                    citekey=new_citekey,
                    record=record,
                    filename_format=pdf_filename_format,
                )
                if old_pdf != new_pdf:
                    change["old_pdf"] = old_pdf
                    change["new_pdf"] = new_pdf
            else:
                # A PDF the user keeps elsewhere is theirs. The destination is
                # computed from `papers_dir` regardless of where the file
                # actually lives, so renaming a citekey used to *move*
                # ~/Documents/my-paper.pdf into the library, silently.
                change["kept_external_pdf"] = old_pdf

        changes.append(change)

    return changes


def _rename_planned_pdfs(
    changes: list[CitekeyChange],
    entries: list[BibtexEntry],
    errors: list[str],
) -> list[tuple[str, str]]:
    """Apply each planned PDF move, returning the moves that succeeded."""
    renamed: list[tuple[str, str]] = []
    #: Absolute source path -> where this batch moved it. Lets a second entry
    #: referencing the same PDF follow it instead of dangling.
    already_renamed: dict[str, str] = {}

    for change in changes:
        entry = entries[change["entry_index"]]
        entry["citekey"] = change["new_citekey"]

        old_pdf = change.get("old_pdf")
        new_pdf = change.get("new_pdf")
        if not old_pdf or not new_pdf:
            continue
        if not os.path.exists(old_pdf):
            # Silently skipping this left the entry pointing at a path an
            # earlier change in the same batch had already renamed away — two
            # entries sharing one PDF produced a dangling `file =` with
            # `errors: []`, and a later `library clean` reported it as missing with
            # no explanation.
            moved_to = already_renamed.get(os.path.abspath(old_pdf))
            if moved_to is not None:
                entry["fields"]["file"] = moved_to
                errors.append(
                    f"{change['old_citekey']} shares its PDF with another entry: "
                    f"{old_pdf} was renamed to {moved_to}, and the reference was "
                    "repointed there"
                )
            else:
                errors.append(
                    f"PDF for {change['old_citekey']} not found at {old_pdf}: "
                    "the reference was left as it was"
                )
            continue

        # os.rename replaces the destination silently; never trade one stored
        # PDF for another.
        if os.path.exists(new_pdf):
            errors.append(
                f"kept PDF for {change['old_citekey']} at {old_pdf}: "
                f"{new_pdf} already exists"
            )
            continue

        try:
            Path(new_pdf).parent.mkdir(parents=True, exist_ok=True)
            os.rename(old_pdf, new_pdf)
        except OSError as exc:
            errors.append(
                f"failed to rename PDF for {change['old_citekey']} → "
                f"{change['new_citekey']}: {exc}"
            )
            continue

        renamed.append((old_pdf, new_pdf))
        already_renamed[os.path.abspath(old_pdf)] = new_pdf
        # Repoint the entry's file= field at the renamed PDF so the reference
        # does not dangle (write honors file_path_style).
        entry["fields"]["file"] = new_pdf
        change["renamed_pdf"] = True

    return renamed


def reindex_library(
    *,
    bib_path: str,
    papers_dir: str,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
    dry_run: bool = True,
    file_path_style: str = "absolute",
) -> ReindexResult:
    """Regenerate citekeys for all entries and fix file references.

    PDF renames and the bib rewrite happen under one exclusive lock: the entry
    count cannot shift underneath the write, and if the write fails every rename
    is undone, so no entry is left with a ``file =`` field pointing at a path
    that no longer exists.

    Returns a dict with ``status``, ``total_entries``, ``changed`` (list of
    ``{old_citekey, new_citekey, renamed_pdf}``, plus ``old_pdf``/``new_pdf``
    when a PDF move is planned), and ``errors``.
    """
    with with_bib_lock(bib_path, shared=dry_run):
        raw, dropped = read_bib_file_raw_with_failures(bib_path)
        entries: list[BibtexEntry] = raw["entries"]
        records: list[NormalizedRecord] = raw["records"]
        # The read is lenient, so an unparseable block arrives here as a shorter
        # library. Reporting the count without saying so made an audit of one
        # entry look like an audit of the file: `entries: 1` for a two-block
        # file, `errors: []`, `status: ok`. The apply path refuses outright, so
        # this was a lying preview rather than a bad write.
        warnings = [*read_bib_notices(bib_path), *dropped]

        if not entries:
            return {
                "status": "ok",
                "bib_path": bib_path,
                "total_entries": 0,
                "changed": [],
                "errors": [],
                "warnings": warnings,
                "backup_path": None,
            }

        changes = plan_reindex(
            entries=entries,
            records=records,
            papers_dir=papers_dir,
            citekey_format=citekey_format,
            pdf_filename_format=pdf_filename_format,
        )
        errors: list[str] = []
        backup_path: Path | None = None

        if dry_run:
            # Both gates the apply path enforces through the rewrite, in its
            # order: the file must be writable at all, then the renamed entries
            # must survive a round-trip. The PDF checks below were already
            # shared; these were not, so a library the real run refuses outright
            # at exit 5 previewed as a feasible rename list at exit 1 — and one
            # of the renames it offered was `" → 2021"`, for the keyless entry
            # that causes the refusal. Raising the same PziError the write
            # raises keeps the two identical, message and exit code included.
            validate_library_parseable(parse_bib_library(read_bib_source(bib_path)))
            validate_bibtex_roundtrip(_entries_with_planned_citekeys(entries, changes))
            # Apply the real run's tests, not a weaker subset: it also refuses
            # to overwrite an existing destination, so previewing a rename the
            # real run declines is a preview of a different command.
            planned_destinations: set[str] = set()
            #: Sources an earlier change in this same preview already moves. The
            #: real run renames a shared PDF once and repoints the later entry;
            #: without this the preview promised a rename per entry, so two
            #: entries sharing a file previewed two clean renames where the run
            #: performs one rename, one repoint and an error.
            planned_sources: set[str] = set()
            for change in changes:
                old_pdf = change.get("old_pdf")
                new_pdf = change.get("new_pdf")
                will_rename = bool(old_pdf) and os.path.exists(str(old_pdf))
                if will_rename and str(old_pdf) in planned_sources:
                    will_rename = False
                    errors.append(
                        f"would repoint {change['old_citekey']} rather than "
                        f"rename: {old_pdf} shares its PDF with another entry"
                    )
                elif will_rename and new_pdf:
                    if os.path.exists(new_pdf) or new_pdf in planned_destinations:
                        will_rename = False
                        errors.append(
                            f"would keep PDF for {change['old_citekey']} at "
                            f"{old_pdf}: {new_pdf} already exists"
                        )
                    else:
                        planned_destinations.add(new_pdf)
                        planned_sources.add(str(old_pdf))
                change["renamed_pdf"] = will_rename
        elif changes:
            # Every citekey in the library is about to change, breaking any
            # `\cite{}` that used the old ones, and there is no undo. `delete`
            # and `library merge` — the other two commands that destroy something
            # the user cannot reconstruct — both leave a `.bak`, and both write
            # it under the lock immediately before the write so it is exactly
            # the content being replaced.
            #
            # Taken *before* a single PDF moves. The renames used to run first,
            # so a failing `mkdir`/`copy2` — disk full, permissions — propagated
            # with every PDF already renamed, the bib untouched, no backup, and
            # nothing telling the user where the files had gone. The undo below
            # guarded only the rewrite; now it guards the renames too.
            backup_path = backup_path_for(bib_path, "reindex")
            backup_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            shutil.copy2(bib_path, backup_path)
            renamed: list[tuple[str, str]] = []
            try:
                renamed = _rename_planned_pdfs(changes, entries, errors)
                rewrite_entries_in_order_locked(
                    bib_path, entries, file_path_style=file_path_style
                )
            except BaseException:
                for old_pdf, new_pdf in reversed(renamed):
                    try:
                        os.rename(new_pdf, old_pdf)
                    except OSError:  # pragma: no cover - best-effort undo
                        pass
                backup_path.unlink(missing_ok=True)
                backup_path = None
                raise

        return {
            "status": "ok",
            "bib_path": bib_path,
            "total_entries": len(entries),
            "changed": [dict(change) for change in changes],
            "errors": errors,
            "warnings": warnings,
            "backup_path": str(backup_path) if backup_path is not None else None,
        }


#: Command names a current pzi could never put in a filename: it decodes LaTeX
#: before sanitizing, so a name containing these is the residue of a title that
#: was stripped rather than decoded. Scoping the default rename to these is what
#: keeps it from touching the ~10.7k files whose names differ from the template
#: only in case and punctuation, because Better BibTeX named them from Zotero's
#: title rather than from the exported one.
_FILENAME_RESIDUE = (
    "textasciicircum", "textbraceleft", "textbraceright", "textbackslash",
    "textasciitilde", "textquotedbl", "textunderscore", "{{", "}}",
    "texttt", "mathcal", "mathrm", "mathbb", "mathscr", "mathsf", "mathfrak",
    "textbf", "textit", "textrm",
)


def rename_files_to_policy(
    *,
    bib_path: str,
    papers_dir: str,
    pdf_filename_format: str | None = None,
    dry_run: bool = True,
    include_all: bool = False,
    file_path_style: str = "absolute",
) -> ReindexResult:
    """Rename attached PDFs to the name ``pdf_filename_format`` now produces.

    The companion to the citekey pass: same command, same lock, same `.bak`,
    same read-only default. It exists because a naming *policy* can change
    without any citekey changing — pzi's own LaTeX decoding changed twice in one
    day, and each time the only way to resync was a hand-written script.

    *include_all* is the difference between a repair and a mass rewrite. By
    default only names carrying LaTeX residue are touched; everything else is
    counted as `skipped_cosmetic` and left alone. On a real library the wider
    set was 10,695 files whose names differ from the template purely in case,
    which is not a defect and must never be swept up by a repair.
    """
    # `shared=dry_run` and the *raw* read, mirroring `reindex_library`:
    # `read_bib_file` acquires the lock itself, so calling it here deadlocks
    # against the lock this function already holds.
    with with_bib_lock(bib_path, shared=dry_run):
        raw, _dropped = read_bib_file_raw_with_failures(bib_path)
        entries = raw["entries"]
        records = raw["records"]

        changed: list[dict[str, Any]] = []
        errors: list[str] = []
        skipped_cosmetic = 0
        planned_destinations: set[str] = set()
        planned_sources: set[str] = set()

        for index, entry in enumerate(entries):
            record = records[index] if index < len(records) else {}
            current = record.get("local_pdf_path")
            if not isinstance(current, str) or not current:
                continue
            citekey = entry.get("citekey", "")
            wanted = format_pdf_filename(
                pdf_filename_format, {**record, "citekey": citekey}
            )
            current_path = Path(current)
            if current_path.name == wanted:
                continue
            if not any(marker in current_path.name for marker in _FILENAME_RESIDUE):
                skipped_cosmetic += 1
                if not include_all:
                    continue

            destination = str(Path(papers_dir) / wanted)
            # The same two refusals the citekey pass makes, for the same
            # reasons: never overwrite an existing file, and never rename a
            # source a previous change in this run already moved.
            if str(current_path) in planned_sources:
                errors.append(
                    f"would repoint {citekey} rather than rename: {current} "
                    "shares its PDF with another entry"
                )
                continue
            if os.path.exists(destination) or destination in planned_destinations:
                errors.append(
                    f"keeping PDF for {citekey} at {current}: "
                    f"{destination} already exists"
                )
                continue
            planned_destinations.add(destination)
            planned_sources.add(str(current_path))
            changed.append({
                "citekey": citekey,
                "old_pdf": str(current_path),
                "new_pdf": destination,
                "renamed_pdf": True,
            })

        backup_path: Path | None = None
        if not dry_run and changed:
            # Backup before anything moves, as the citekey pass now does.
            backup_path = backup_path_for(bib_path, "reindex")
            backup_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            shutil.copy2(bib_path, backup_path)
            moved: list[tuple[str, str]] = []
            try:
                for change in changed:
                    os.rename(change["old_pdf"], change["new_pdf"])
                    moved.append((change["old_pdf"], change["new_pdf"]))
                for index, entry in enumerate(entries):
                    record = records[index] if index < len(records) else {}
                    for change in changed:
                        if record.get("local_pdf_path") == change["old_pdf"]:
                            entry.setdefault("fields", {})["file"] = change["new_pdf"]
                rewrite_entries_in_order_locked(
                    bib_path, entries, file_path_style=file_path_style
                )
            except BaseException:
                for old_pdf, new_pdf in reversed(moved):
                    try:
                        os.rename(new_pdf, old_pdf)
                    except OSError:  # pragma: no cover - best-effort undo
                        pass
                backup_path.unlink(missing_ok=True)
                raise

        return {
            "status": "ok",
            "bib_path": bib_path,
            "total_entries": len(entries),
            "changed": changed,
            "errors": errors,
            "warnings": [],
            "backup_path": str(backup_path) if backup_path else None,
            "skipped_cosmetic": skipped_cosmetic,
        }
