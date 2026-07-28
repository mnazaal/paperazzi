"""Library integrity checks — parse validation, orphan PDFs, missing PDFs."""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.bib_repository import (
    read_bib_file_raw,
    validate_library_parseable,
    with_bib_lock,
)
from pzi.bibtex import BibtexEntry
from pzi.fileio import read_text_utf8
from pzi.pdf_planning import pdf_file_present

QUARANTINE_DIRNAME = ".orphans"


class CleanResult(TypedDict):
    status: str
    bib_path: str
    papers_dir: str
    total_entries: int
    duplicate_citekeys: list[str]
    missing_pdfs: list[str]
    orphan_pdfs: list[str]
    issues: list[dict[str, Any]]
    actions: NotRequired[list[dict[str, Any]]]


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
        raw = read_bib_file_raw(bib_path)
    entries: list[BibtexEntry] = raw["entries"]
    records = raw["records"]

    # Parse-check before anything else, and before the empty-library shortcut.
    # `read_bib_file_raw` is lenient: it drops blocks it cannot parse instead of
    # raising, so a malformed file arrives here looking like a short — or
    # entirely empty — library. Checking after the shortcut meant a wholly
    # corrupt bib reported "ok, 0 entries, no issues" and exited 0. Worse, a
    # partially corrupt one let `--fix` quarantine the PDFs of every dropped
    # entry: a dropped entry contributes no referenced path, so its PDF looks
    # orphaned, and moving it leaves the entry's `file =` dangling.
    if Path(bib_path).exists():
        try:
            from bibtexparser.entrypoint import parse_string as _parse
            text = read_text_utf8(bib_path)
            library = _parse(text)
            validate_library_parseable(library)
        except ValueError as exc:
            # Every count below is derived from the entries the lenient parser
            # kept, so all of them would understate a file we cannot fully read.
            # Report the parse failure alone rather than alongside numbers that
            # invite acting on it.
            return {
                "status": "error",
                "bib_path": bib_path,
                "papers_dir": papers_dir,
                "total_entries": len(entries),
                "duplicate_citekeys": [],
                "missing_pdfs": [],
                "orphan_pdfs": [],
                "issues": [{
                    "severity": "error",
                    "type": "parse_error",
                    "message": str(exc),
                }],
            }

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
        if pdf_path and not pdf_file_present(pdf_path):
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
        if pdf and pdf_file_present(pdf):
            referenced_paths.add(os.path.realpath(str(Path(str(pdf)).expanduser())))

    orphan_pdfs: list[str] = []
    papers = Path(papers_dir)
    if papers.is_dir():
        for pdf_file in papers.rglob("*.pdf"):
            # Files already quarantined are not loose orphans; re-detecting them
            # would keep reporting issues forever after the first --fix run.
            if QUARANTINE_DIRNAME in pdf_file.relative_to(papers).parts:
                continue
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


def plan_orphan_quarantine(
    *,
    orphan_pdfs: list[str],
    orphan_dir: str,
    taken_names: set[str],
) -> list[dict[str, Any]]:
    """Choose a free destination under the quarantine directory for each orphan.

    Pure.  The quarantine directory is an archive, so a basename already taken —
    whether by an earlier run (*taken_names*) or by an earlier orphan in this
    same batch — gets a numbered suffix rather than overwriting what is stored.
    """
    used = set(taken_names)
    moves: list[dict[str, Any]] = []

    for source in orphan_pdfs:
        src = Path(source)
        name = src.name
        attempt = 1
        while name in used:
            name = f"{src.stem}-{attempt}{src.suffix}"
            attempt += 1
        used.add(name)
        moves.append({
            "type": "move_orphan",
            "source": str(src),
            "destination": str(Path(orphan_dir) / name),
        })

    return moves


def _names_in_dir(directory: Path) -> set[str]:
    """Return the filenames already present in *directory* (empty if absent)."""
    try:
        return {child.name for child in directory.iterdir()}
    except OSError:
        return set()


def clean_library(
    *,
    bib_path: str,
    papers_dir: str,
    dry_run: bool = True,
    move_orphans: bool = True,
) -> CleanResult:
    """Fix integrity issues in a BibTeX library.

    - ``move_orphans``: move orphan PDFs to ``papers_dir/.orphans/``

    Only the filesystem is touched (orphan PDFs are relocated); the ``.bib``
    file itself is never rewritten, so comments, ``@string``/``@preamble``
    macros, and source formatting are left intact.

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
        orphan_dir = Path(papers_dir) / QUARANTINE_DIRNAME
        actions = plan_orphan_quarantine(
            orphan_pdfs=validation["orphan_pdfs"],
            orphan_dir=str(orphan_dir),
            taken_names=_names_in_dir(orphan_dir),
        )
        if not dry_run:
            orphan_dir.mkdir(parents=True, exist_ok=True)
            for action in actions:
                try:
                    shutil.move(action["source"], action["destination"])
                    action["done"] = True
                except OSError as exc:
                    action["done"] = False
                    action["error"] = str(exc)

    validation["actions"] = actions
    return validation
