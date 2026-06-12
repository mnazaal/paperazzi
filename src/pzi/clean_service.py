"""Library integrity checks — parse validation, orphan PDFs, missing PDFs."""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, TypeAlias

from pzi.bib_repository import (
    _read_bib_file_raw,
    _validate_library_parseable,
    parse_bibtex,
    serialize_bibtex,
    with_bib_lock,
)
from pzi.bibtex import BibtexEntry


CleanResult: TypeAlias = dict[str, Any]


def validate_library(
    *,
    bib_path: str,
    papers_dir: str,
) -> CleanResult:
    """Check a BibTeX library for integrity issues.

    Returns a dict with:
    - ``status``: ``"ok"`` or ``"error"`` (parse failure)
    - ``issues``: list of issue dicts (severity, type, message)
    - ``total_entries``, ``duplicate_citekeys``, ``missing_pdfs``, ``orphan_pdfs``
    """
    issues: list[dict[str, Any]] = []

    # --- Parse validation ---
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
    entries: list[BibtexEntry] = raw["entries"]
    records = raw["records"]

    if not entries:
        return {
            "status": "ok",
            "bib_path": bib_path,
            "papers_dir": papers_dir,
            "total_entries": 0,
            "duplicate_citekeys": [],
            "missing_pdfs": [],
            "orphan_pdfs": [],
            "issues": [],
        }

    try:
        from bibtexparser import parse_string as _parse
        text = Path(bib_path).read_text(encoding="utf-8")
        library = _parse(text)
        _validate_library_parseable(library)
    except ValueError as exc:
        issues.append({
            "severity": "error",
            "type": "parse_error",
            "message": str(exc),
        })

    # --- Duplicate citekeys ---
    citekey_counts = Counter(entry["citekey"] for entry in entries)
    duplicate_citekeys = sorted(k for k, v in citekey_counts.items() if v > 1)
    for dk in duplicate_citekeys:
        issues.append({
            "severity": "error",
            "type": "duplicate_citekey",
            "message": f"citekey {dk} appears {citekey_counts[dk]} times",
        })

    # --- Missing PDFs ---
    missing_pdfs: list[str] = []
    for record in records:
        pdf_path = record.get("local_pdf_path")
        if pdf_path and not os.path.exists(str(pdf_path)):
            citekey = record.get("citekey", "?")
            missing_pdfs.append(str(pdf_path))
            issues.append({
                "severity": "warning",
                "type": "missing_pdf",
                "message": f"PDF not found for {citekey}: {pdf_path}",
            })

    # --- Orphan PDFs ---
    referenced_paths: set[str] = set()
    for record in records:
        pdf = record.get("local_pdf_path")
        if pdf and os.path.exists(str(pdf)):
            referenced_paths.add(os.path.realpath(str(pdf)))

    orphan_pdfs: list[str] = []
    papers = Path(papers_dir)
    if papers.is_dir():
        for pdf_file in papers.rglob("*.pdf"):
            real = os.path.realpath(str(pdf_file))
            if real not in referenced_paths:
                orphan_pdfs.append(str(pdf_file))
                issues.append({
                    "severity": "warning",
                    "type": "orphan_pdf",
                    "message": f"orphan PDF: {pdf_file.name}",
                })

    return {
        "status": "ok",
        "bib_path": bib_path,
        "papers_dir": papers_dir,
        "total_entries": len(entries),
        "duplicate_citekeys": duplicate_citekeys,
        "missing_pdfs": missing_pdfs,
        "orphan_pdfs": orphan_pdfs,
        "issues": issues,
    }


def clean_library(
    *,
    bib_path: str,
    papers_dir: str,
    dry_run: bool = True,
    move_orphans: bool = True,
    sort_entries: bool = True,
) -> CleanResult:
    """Fix integrity issues in a BibTeX library.

    - ``move_orphans``: move orphan PDFs to ``papers_dir/.orphans/``
    - ``sort_entries``: re-serialize entries sorted by citekey

    Returns the same shape as :func:`validate_library` with an added
    ``actions`` list describing what was (or would be) done.
    """
    validation = validate_library(bib_path=bib_path, papers_dir=papers_dir)
    actions: list[dict[str, Any]] = []

    if validation["status"] == "error":
        validation["actions"] = actions
        return validation

    # --- Move orphan PDFs ---
    if move_orphans and validation["orphan_pdfs"]:
        orphan_dir = Path(papers_dir) / ".orphans"
        for pdf_path_str in validation["orphan_pdfs"]:
            src = Path(pdf_path_str)
            dst = orphan_dir / src.name
            action = {
                "type": "move_orphan",
                "source": str(src),
                "destination": str(dst),
            }
            if not dry_run:
                orphan_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(src), str(dst))
                    action["done"] = True
                except OSError as exc:
                    action["done"] = False
                    action["error"] = str(exc)
            actions.append(action)

    # --- Sort entries ---
    if sort_entries:
        with with_bib_lock(bib_path, shared=True):
            raw = _read_bib_file_raw(bib_path)
        entries = raw["entries"]
        if len(entries) > 1:
            sorted_entries = sorted(entries, key=lambda e: e["citekey"].lower())
            action = {"type": "sort_entries", "count": len(entries)}
            if not dry_run:
                text = serialize_bibtex(sorted_entries)
                bib_file = Path(bib_path)
                bib_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                bib_file.write_text(text, encoding="utf-8")
                action["done"] = True
            actions.append(action)

    validation["actions"] = actions
    return validation
