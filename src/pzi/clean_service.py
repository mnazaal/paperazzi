"""Library integrity checks — parse validation, orphan PDFs, missing PDFs."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.bib_repository import (
    read_bib_file,
    read_bib_file_with_failures,
    read_bib_notices,
)
from pzi.bib_serialize import failed_block_details, validate_bibtex_roundtrip
from pzi.bibtex import BibtexEntry
from pzi.config import AppConfig, BibConfig
from pzi.errors import REASON_USAGE, PziError
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
    #: Structured failure discriminator (see `pzi.errors`). Only set on the
    #: parse-failure path — the file's content is malformed, not a bad flag or
    #: an unreachable dependency.
    reason: NotRequired[str]
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
            # A malformed block in the file's own content, not a bad flag or
            # an unavailable dependency — the fix is to correct the file, not
            # retry. Same class as `attach_pdf_bytes`'s "invalid PDF payload".
            "reason": REASON_USAGE,
            # The documented `--json` failure channel must not be empty.
            "errors": parse_failures,
            "issues": [
                {"severity": "error", "type": "parse_error", "message": message}
                for message in parse_failures
            ],
        }

    # --- Entries that parse but cannot be written back ---
    #
    # An entry can read fine and still be unrepresentable — a field key with a
    # space in it is the reachable case, and it is not legal BibTeX. Until
    # 2026-08-23 this surfaced only as a side effect: the write gate round-tripped
    # the *whole* library, so one such entry made every unrelated write refuse.
    # Item 567 scoped that gate to the entries a write touches (a ~50% saving on
    # every write), which means the condition now has to be looked for
    # deliberately — and `clean` is where a library's health is reported.
    #
    # Checked per entry rather than as one library round-trip, so the report can
    # name which entry is at fault instead of failing the whole run.
    for entry in entries:
        try:
            validate_bibtex_roundtrip([entry])
        except PziError as exc:
            issues.append({
                "severity": "error",
                "type": "unwritable_entry",
                "message": (
                    f"{entry.get('citekey') or '<no citekey>'}: reads back but "
                    f"cannot be written — {exc.message}"
                ),
            })

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
    sibling_paths, sibling_errors = referenced_pdf_paths(sibling_bib_paths)
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


def referenced_pdf_paths(
    bib_paths: Sequence[str],
    *,
    excluding_citekeys: Sequence[str] = (),
) -> tuple[set[str], list[str]]:
    """Resolved PDF paths the given libraries reference, and any that would not read.

    A sibling is read leniently, exactly as the target is; the difference is that
    an unreadable one is reported rather than skipped, because "no references"
    and "references we could not see" are indistinguishable from here and only
    one of them is safe to quarantine against.

    *excluding_citekeys* drops entries that are on their way out. A `--dry-run`
    disposal asks this question *before* the write, so the entry being removed is
    still in the file and still names its own PDF: without the exclusion the
    preview reported "still referenced by another entry" for a file the real run
    quarantines — a preview contradicting the run it previews.
    """
    dropped = set(excluding_citekeys)
    referenced: set[str] = set()
    errors: list[str] = []
    for sibling in bib_paths:
        if not Path(sibling).exists():
            continue
        raw, failures = read_bib_file_with_failures(sibling)
        for message in failures:
            errors.append(f"{Path(sibling).name}: {message}")
        for record in raw["records"]:
            if record.get("citekey") in dropped:
                continue
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


class QuarantineResult(TypedDict):
    """What became of one PDF a removing command orphaned."""

    #: "moved", "kept" (still referenced, or outside `papers_dir`), "missing"
    #: (nothing at the path), or "failed" (the move raised).
    status: str
    source: str
    destination: NotRequired[str]
    #: Why a "kept" file was left alone, for the caller to print verbatim.
    reason: NotRequired[str]
    error: NotRequired[str]
    #: Present (True) only on a preview: the move was planned, not performed.
    dry_run: NotRequired[bool]


def quarantine_pdf(
    *,
    pdf_path: str,
    papers_dir: str,
    dry_run: bool = False,
) -> QuarantineResult:
    """File one orphaned PDF into ``papers_dir/.orphans/``.

    The targeted counterpart to :func:`clean_library`'s sweep, for the command
    that *caused* the orphan and therefore already knows which file it is.
    Knowing the path is what makes this safe where the sweep is not: the sweep
    must refuse whenever any `file =` field fails to resolve, because it infers
    orphanhood from the complement of the referenced set, and an incomplete set
    would have it move a PDF the library still wants. Nothing is inferred here.

    Moves, never deletes — the file stays recoverable next to the `.bak` the
    caller wrote, so the two together undo the whole operation.
    """
    source = Path(str(pdf_path)).expanduser()
    if not pdf_file_present(str(source)):
        return {"status": "missing", "source": str(source)}

    orphan_dir = Path(papers_dir) / QUARANTINE_DIRNAME
    planned = plan_orphan_quarantine(
        orphan_pdfs=[str(source)],
        orphan_dir=str(orphan_dir),
        taken_names=_names_in_dir(orphan_dir),
    )
    destination = str(planned[0]["destination"])
    if dry_run:
        # The preview action names itself: `api.delete`/`api.merge` default to
        # previewing and hand the raw dict to the caller, who otherwise cannot
        # tell `"moved"` from "would move" — the CLI's tense lives in a renderer
        # the facade never runs.
        return {
            "status": "moved",
            "source": str(source),
            "destination": destination,
            "dry_run": True,
        }

    try:
        # Creating the quarantine directory is part of the move: an unwritable
        # papers_dir raised out of here as a traceback where the caller expects
        # a "failed" action it can report and exit 1 on.
        orphan_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), destination)
    except OSError as exc:
        return {
            "status": "failed",
            "source": str(source),
            "destination": destination,
            "error": str(exc),
        }
    return {"status": "moved", "source": str(source), "destination": destination}


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


def siblings_sharing_papers_dir(config: AppConfig, target: BibConfig) -> list[str]:
    """The other configured libraries storing PDFs in *target*'s papers directory.

    The default layout gives every bib the same `papers_dir`, so without this a
    check of one library reported the others' PDFs as orphans — and `--fix`
    quarantined them, breaking `file =` fields in a library the user never named.
    """
    shared = os.path.realpath(target["papers_dir"])
    return [
        bib["path"]
        for bib in config.get("bibs", [])
        if bib["path"] != target["path"]
        and os.path.realpath(bib["papers_dir"]) == shared
    ]


def plan_pdf_disposal(
    *,
    result: Mapping[str, Any],
    config: AppConfig,
    target: BibConfig,
    keep_pdf: bool,
    dry_run: bool,
    removed_citekeys: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Quarantine the PDF a just-removed entry orphaned, when it is safe to.

    Shared by `delete` and `library merge` because both drop an entry and strand
    the same kind of file; the two diverged once already, and a hazard checked in
    one of them is not checked at all.

    *removed_citekeys* names every entry the whole operation removes — for a
    batch delete, all its citekeys, not only this result's. Each entry's own
    citekey is excluded regardless; without the rest, a batch `--dry-run` on two
    entries sharing one PDF saw the *other* doomed entry still referencing the
    file and previewed "left — still referenced" for a file the real run
    quarantines.

    Returns ``None`` when there is nothing to decide (no PDF, or `--keep-pdf`),
    otherwise a :class:`~pzi.clean_service.QuarantineResult` the caller reports.
    """
    pdf_path = result.get("pdf_path") or result.get("orphaned_pdf")
    if keep_pdf or not isinstance(pdf_path, str) or not pdf_path.strip():
        return None

    papers_dir = target["papers_dir"]
    resolved = os.path.realpath(os.path.expanduser(pdf_path))

    # Outside the tree pzi manages, so pzi does not move it. The user's
    # `papers_dir` is one directory; a `file =` field can point anywhere, and
    # relocating something from an unrelated folder is a surprise no `.orphans/`
    # note makes up for.
    if os.path.commonpath([resolved, os.path.realpath(papers_dir)]) != os.path.realpath(
        papers_dir
    ):
        return {
            "status": "kept",
            "source": pdf_path,
            "reason": "outside the library's papers directory",
        }

    # A second entry can name the same file. Under the default `{citekey}.pdf`
    # naming two entries never collide, but a content-derived
    # `pdf_filename_format` renders one name for two duplicates, and
    # `resolve_pdf_destination` hands back the *existing* file when the bytes
    # match — so the duplicate pair this command exists to reconcile is exactly
    # where one path ends up in two entries. An imported `.bib` can carry the
    # same sharing outright.
    still_referenced, read_errors = referenced_pdf_paths(
        [target["path"], *siblings_sharing_papers_dir(config, target)],
        # On a real run these entries are already gone and this is a no-op; on a
        # preview they are still in the file and would match their own PDF.
        excluding_citekeys=[
            key
            for key in (
                result.get("citekey"),
                result.get("dropped_citekey"),
                *removed_citekeys,
            )
            if isinstance(key, str)
        ],
    )
    # An unreadable block contributes no referenced path, so "not in the set"
    # over an incomplete set proves nothing — the same reasoning that makes
    # `clean_library` refuse to sweep on a partial parse. The audit reproduced
    # the failure: a sibling with an unbalanced brace could not veto the move,
    # and the delete quarantined a PDF the sibling's intact `file =` named.
    if read_errors:
        return {
            "status": "kept",
            "source": pdf_path,
            "reason": f"could not verify references: {read_errors[0]}",
        }
    if resolved in still_referenced:
        return {
            "status": "kept",
            "source": pdf_path,
            "reason": "still referenced by another entry",
        }

    return dict(
        quarantine_pdf(pdf_path=pdf_path, papers_dir=papers_dir, dry_run=dry_run)
    )
