"""Citekey regeneration and file-reference repair."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.bib_repository import (
    read_bib_file_raw,
    rewrite_entries_in_order_locked,
    with_bib_lock,
)
from pzi.bibtex import BibtexEntry, NormalizedRecord
from pzi.format_templates import format_citekey
from pzi.pdf_planning import plan_pdf_path


class CitekeyChange(TypedDict):
    """One planned citekey rename, with the PDF move it implies."""

    entry_index: int
    old_citekey: str
    new_citekey: str
    renamed_pdf: bool
    old_pdf: NotRequired[str]
    new_pdf: NotRequired[str]


class ReindexResult(TypedDict):
    status: str
    bib_path: str
    total_entries: int
    changed: list[dict[str, Any]]
    errors: list[str]


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

        existing_keys.discard(old_citekey)
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
            new_pdf = plan_pdf_path(
                papers_dir=papers_dir,
                citekey=new_citekey,
                record=record,
                filename_format=pdf_filename_format,
            )
            if old_pdf != new_pdf:
                change["old_pdf"] = old_pdf
                change["new_pdf"] = new_pdf

        changes.append(change)

    return changes


def _rename_planned_pdfs(
    changes: list[CitekeyChange],
    entries: list[BibtexEntry],
    errors: list[str],
) -> list[tuple[str, str]]:
    """Apply each planned PDF move, returning the moves that succeeded."""
    renamed: list[tuple[str, str]] = []

    for change in changes:
        entry = entries[change["entry_index"]]
        entry["citekey"] = change["new_citekey"]

        old_pdf = change.get("old_pdf")
        new_pdf = change.get("new_pdf")
        if not old_pdf or not new_pdf or not os.path.exists(old_pdf):
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
        raw = read_bib_file_raw(bib_path)
        entries: list[BibtexEntry] = raw["entries"]
        records: list[NormalizedRecord] = raw["records"]

        if not entries:
            return {
                "status": "ok",
                "bib_path": bib_path,
                "total_entries": 0,
                "changed": [],
                "errors": [],
            }

        changes = plan_reindex(
            entries=entries,
            records=records,
            papers_dir=papers_dir,
            citekey_format=citekey_format,
            pdf_filename_format=pdf_filename_format,
        )
        errors: list[str] = []

        if dry_run:
            for change in changes:
                old_pdf = change.get("old_pdf")
                change["renamed_pdf"] = bool(old_pdf) and os.path.exists(str(old_pdf))
        elif changes:
            renamed = _rename_planned_pdfs(changes, entries, errors)
            try:
                rewrite_entries_in_order_locked(
                    bib_path, entries, file_path_style=file_path_style
                )
            except BaseException:
                for old_pdf, new_pdf in reversed(renamed):
                    try:
                        os.rename(new_pdf, old_pdf)
                    except OSError:  # pragma: no cover - best-effort undo
                        pass
                raise

        return {
            "status": "ok",
            "bib_path": bib_path,
            "total_entries": len(entries),
            "changed": [dict(change) for change in changes],
            "errors": errors,
        }
