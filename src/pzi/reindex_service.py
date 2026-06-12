"""Citekey regeneration and file-reference repair."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeAlias

from pzi.bib_repository import (
    _read_bib_file_raw,
    serialize_bibtex,
    with_bib_lock,
)
from pzi.bibtex import bibtex_entry_to_record
from pzi.format_templates import format_citekey
from pzi.pdf_planning import plan_pdf_path

ReindexResult: TypeAlias = dict[str, Any]


def reindex_library(
    *,
    bib_path: str,
    papers_dir: str,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
    dry_run: bool = True,
) -> ReindexResult:
    """Regenerate citekeys for all entries and fix file references.

    Returns a dict with ``status``, ``total_entries``, ``changed`` (list of
    ``{old_citekey, new_citekey, renamed_pdf}``), and ``errors``.
    """
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)

    entries = raw["entries"]
    records = raw["records"]

    if not entries:
        return {
            "status": "ok",
            "bib_path": bib_path,
            "total_entries": 0,
            "changed": [],
            "errors": [],
        }

    # Track existing citekeys to avoid collisions during reindex
    existing_keys: set[str] = {entry["citekey"] for entry in entries}
    changed: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, entry in enumerate(entries):
        old_citekey = entry["citekey"]
        record = records[i] if i < len(records) else {}

        # Generate new citekey
        new_base = format_citekey(citekey_format, record, existing_keys - {old_citekey})
        new_citekey = new_base

        if new_citekey == old_citekey:
            continue  # no change

        changed.append({
            "old_citekey": old_citekey,
            "new_citekey": new_citekey,
            "renamed_pdf": False,
        })

        # Update entry citekey
        entry["citekey"] = new_citekey
        existing_keys.discard(old_citekey)
        existing_keys.add(new_citekey)

        # Rename PDF if it exists
        old_pdf_path = plan_pdf_path(
            papers_dir=papers_dir,
            citekey=old_citekey,
            record=record,
            filename_format=pdf_filename_format,
        )
        new_pdf_path = plan_pdf_path(
            papers_dir=papers_dir,
            citekey=new_citekey,
            record=record,
            filename_format=pdf_filename_format,
        )

        if os.path.exists(old_pdf_path) and old_pdf_path != new_pdf_path:
            if not dry_run:
                try:
                    Path(new_pdf_path).parent.mkdir(parents=True, exist_ok=True)
                    os.rename(old_pdf_path, new_pdf_path)
                    changed[-1]["renamed_pdf"] = True
                except OSError as exc:
                    errors.append(
                        f"failed to rename PDF for {old_citekey} → {new_citekey}: {exc}"
                    )
            else:
                changed[-1]["renamed_pdf"] = True
                changed[-1]["old_pdf"] = old_pdf_path
                changed[-1]["new_pdf"] = new_pdf_path

    # Write back if not dry run
    if changed and not dry_run:
        sorted_entries = sorted(entries, key=lambda e: e["citekey"].lower())
        text = serialize_bibtex(sorted_entries)
        bib_file = Path(bib_path)
        bib_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        bib_file.write_text(text, encoding="utf-8")

    return {
        "status": "ok",
        "bib_path": bib_path,
        "total_entries": len(entries),
        "changed": changed,
        "errors": errors,
    }
