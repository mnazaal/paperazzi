"""Library integrity checks — parse validation, orphan PDFs, missing PDFs."""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.bib_repository import (
    read_bib_file,
    read_bib_file_with_failures,
    read_bib_notices,
)
from pzi.bib_serialize import failed_block_details
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
    #: True when the parser dropped a block, so the counts above describe only
    #: what could be read. `clean_library` refuses to quarantine anything while
    #: this holds.
    partial_parse: bool
    errors: list[str]
    issues: list[dict[str, Any]]
    #: Read notices that are not about an individual block — currently only
    #: "the configured bib does not exist". Printed by `print_read_warnings`.
    warnings: NotRequired[list[str]]
    actions: NotRequired[list[dict[str, Any]]]


def validate_library(
    *,
    bib_path: str,
    papers_dir: str,
    sibling_bib_paths: Sequence[str] = (),
) -> CleanResult:
    """Check a BibTeX library for integrity issues.

    *sibling_bib_paths* are the other configured libraries sharing *papers_dir*.
    A PDF any of them references is not an orphan: the default layout points
    every bib at one ``papers_dir``, so without this, checking one library saw
    the others' PDFs as unreferenced and ``--fix`` quarantined them, breaking
    those libraries' ``file =`` fields.

    Returns a dict with:
    - ``status``: ``"ok"`` or ``"error"`` (parse failure)
    - ``issues``: list of issue dicts (severity, type, message)
    - ``total_entries``, ``duplicate_citekeys``, ``missing_pdfs``, ``orphan_pdfs``
    """
    issues: list[dict[str, Any]] = []

    # --- Parse validation ---
    raw = read_bib_file(bib_path)
    entries: list[BibtexEntry] = raw["entries"]
    records = raw["records"]

    # `read_bib_file` is lenient: it drops blocks it cannot parse instead of
    # raising, so a malformed file arrives here looking like a short — or
    # entirely empty — library. Classify what was dropped, because the two kinds
    # warrant different answers:
    #
    #   * a duplicate citekey leaves a *readable* file with one reported entry
    #     missing. That is a finding to report, not a reason to refuse.
    #   * an unparseable block means the file cannot be trusted as a whole, so
    #     every count below would understate it. Report the failure alone rather
    #     than alongside numbers that invite acting on them.
    duplicate_citekeys: list[str] = []
    parse_failures: list[str] = []
    if Path(bib_path).exists():
        from bibtexparser.entrypoint import parse_string as _parse

        library = _parse(read_text_utf8(bib_path))
        for key, message in failed_block_details(library):
            if key is None:
                parse_failures.append(message)
            else:
                duplicate_citekeys.append(key)
                issues.append({
                    "severity": "error",
                    "type": "duplicate_citekey",
                    "message": message,
                })

    if parse_failures:
        return {
            "status": "error",
            "bib_path": bib_path,
            "papers_dir": papers_dir,
            "total_entries": len(entries),
            "duplicate_citekeys": [],
            "missing_pdfs": [],
            "orphan_pdfs": [],
            "partial_parse": True,
            # The documented `--json` failure channel must not be empty.
            "errors": parse_failures,
            "issues": [
                {"severity": "error", "type": "parse_error", "message": message}
                for message in parse_failures
            ],
        }

    duplicate_citekeys = sorted(set(duplicate_citekeys))
    # True whenever the parser dropped something. `clean_library` keys the
    # orphan quarantine off this rather than off `status`, because a dropped
    # entry contributes no referenced path — so its PDF looks orphaned, and
    # moving it would leave the entry's `file =` dangling.
    partial_parse = bool(duplicate_citekeys)

    # An empty bibliography does not mean an empty papers directory: with no
    # entries, *every* stored PDF is an orphan, which is exactly what the user
    # needs told. Returning early here reported `orphan_pdfs: []` for a papers
    # dir full of files.
    if not entries and not _names_in_dir(Path(papers_dir)):
        return {
            "status": "ok",
            "bib_path": bib_path,
            "papers_dir": papers_dir,
            "total_entries": 0,
            "duplicate_citekeys": duplicate_citekeys,
            "missing_pdfs": [],
            "orphan_pdfs": [],
            "partial_parse": partial_parse,
            "errors": [],
            "issues": issues,
            "warnings": read_bib_notices(bib_path),
        }

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

    # A sibling library pointed at the same `papers_dir` references PDFs too, and
    # they are just as much not-orphans as this library's own.
    sibling_paths, sibling_errors = _referenced_by_siblings(sibling_bib_paths)
    referenced_paths |= sibling_paths
    if sibling_errors:
        # Same reasoning as `partial_parse` below: an incomplete reference set is
        # how a referenced PDF gets quarantined, and a sibling we could not read
        # contributes none of its own.
        partial_parse = True
        for message in sibling_errors:
            issues.append({
                "severity": "error",
                "type": "sibling_parse_error",
                "message": message,
            })

    orphan_pdfs: list[str] = []
    papers = Path(papers_dir)
    # Skipped entirely under a partial parse. Orphan detection needs the
    # *complete* set of referenced paths, and a dropped duplicate contributes
    # none — so its PDF would be reported as orphaned, and `--fix` would move a
    # file the library still refers to.
    if papers.is_dir() and not partial_parse:
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
        "partial_parse": partial_parse,
        "errors": sibling_errors,
        "issues": issues,
        "warnings": read_bib_notices(bib_path),
    }


def _referenced_by_siblings(
    sibling_bib_paths: Sequence[str],
) -> tuple[set[str], list[str]]:
    """Resolved PDF paths the other libraries reference, and any that would not read.

    A sibling is read leniently, exactly as the target is; the difference is that
    an unreadable one is reported rather than skipped, because "no references"
    and "references we could not see" are indistinguishable from here and only
    one of them is safe to quarantine against.
    """
    referenced: set[str] = set()
    errors: list[str] = []
    for sibling in sibling_bib_paths:
        if not Path(sibling).exists():
            continue
        raw, failures = read_bib_file_with_failures(sibling)
        for message in failures:
            errors.append(f"{Path(sibling).name}: {message}")
        for record in raw["records"]:
            pdf = record.get("local_pdf_path")
            if pdf:
                referenced.add(os.path.realpath(str(Path(str(pdf)).expanduser())))
    return referenced, errors


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
    sibling_bib_paths: Sequence[str] = (),
) -> CleanResult:
    """Fix integrity issues in a BibTeX library.

    - ``move_orphans``: move orphan PDFs to ``papers_dir/.orphans/``
    - ``sibling_bib_paths``: other libraries sharing *papers_dir*, whose
      referenced PDFs are not this library's orphans (see
      :func:`validate_library`)

    Only the filesystem is touched (orphan PDFs are relocated); the ``.bib``
    file itself is never rewritten, so comments, ``@string``/``@preamble``
    macros, and source formatting are left intact.

    Returns the same shape as :func:`validate_library` with an added
    ``actions`` list describing what was (or would be) done.
    """
    validation = validate_library(
        bib_path=bib_path, papers_dir=papers_dir, sibling_bib_paths=sibling_bib_paths
    )
    actions: list[dict[str, Any]] = []

    # `partial_parse` as well as `status`, and this is load-bearing: duplicates
    # no longer set an error status, so keying only off `status` would let a
    # dropped duplicate's PDF be quarantined -- moving a file the library still
    # references. validate_library already leaves orphan_pdfs empty in that
    # case; this is the second lock on the same door.
    if validation["status"] == "error" or validation.get("partial_parse"):
        validation["actions"] = actions
        return validation

    # An entry whose `file =` does not resolve means the set of referenced paths
    # is incomplete, which is the same hazard `partial_parse` guards — so the
    # quarantine is off, while detection and reporting stay on so the user can
    # see both lists and decide.
    #
    # Two ways this bites. A Zotero/JabRef export writes
    # `file = {Full Text PDF:/path/x.pdf:application/pdf}`, which is read as one
    # nonexistent path: the entry contributes no referenced path, so its real
    # PDF is reported as an orphan *and* as missing in the same run, and `--fix`
    # acted on the second half — detaching every attachment in an imported
    # library. Separately, a genuinely missing PDF often means the file was
    # renamed, in which case the loose file about to be quarantined may be the
    # very one the entry wants.
    if move_orphans and validation["missing_pdfs"]:
        validation["issues"].append({
            "severity": "warning",
            "type": "quarantine_skipped",
            "message": (
                f"not quarantining {len(validation['orphan_pdfs'])} orphan PDF(s): "
                f"{len(validation['missing_pdfs'])} entr(ies) reference a PDF that "
                "could not be resolved, so the set of referenced files is "
                "incomplete. Fix those references first."
            ),
        })
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
    if not dry_run:
        # An orphan that was quarantined is no longer an outstanding issue. The
        # issue list is built by `validate_library` *before* the move, so a
        # successful `--fix` kept reporting every orphan it had just filed away
        # — and the runner turns a non-empty issue list into exit 1, so
        # `pzi library clean --fix && next-step` could never proceed on success.
        moved = {action["source"] for action in actions if action.get("done")}
        if moved:
            moved_names = {Path(source).name for source in moved}
            validation["issues"] = [
                issue
                for issue in validation["issues"]
                if not (
                    issue.get("type") == "orphan_pdf"
                    and issue.get("message", "").removeprefix("orphan PDF: ")
                    in moved_names
                )
            ]
            validation["orphan_pdfs"] = [
                path for path in validation["orphan_pdfs"] if path not in moved
            ]
    failures = [
        f"could not move {action['source']} to {action['destination']}: {action['error']}"
        for action in actions
        if action.get("error")
    ]
    if failures:
        # An action that failed is an error of the run, not a detail of one
        # item: `status: "ok"` with `errors: []` said the quarantine had
        # happened when nothing had moved.
        validation["errors"] = [*(validation.get("errors") or []), *failures]
        validation["status"] = "error"
    return validation
