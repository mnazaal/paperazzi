"""Bulk BibTeX import — import entries from a .bib file into a target library."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.add_service import add_records_to_bib_batch
from pzi.bib_repository import parse_bibtex
from pzi.bibtex import bibtex_entry_to_record
from pzi.config import load_and_resolve_bib


class ImportResult(TypedDict):
    status: str
    source_path: str
    message: str
    errors: list[str]
    total_source: int
    imported: int
    skipped_duplicates: int
    skipped_errors: int
    results: list[dict[str, Any]]
    skipped_in_source: NotRequired[int]


def import_from_bibtex(
    *,
    config_path: str,
    home_dir: str,
    source_path: str,
    bib_selector: str | None = None,
    dry_run: bool = False,
    force_new: bool = False,
) -> ImportResult:
    """Import entries from a BibTeX file into the configured target library.

    Deduplicates against the target library using DOI/arXiv ID/URL matching.
    Returns a dict with import status, per-entry results, and summary counts.
    """
    source = Path(source_path)
    if not source.exists():
        return {
            "status": "error",
            "source_path": source_path,
            "message": "source file not found",
            "errors": [f"file not found: {source_path}"],
            "total_source": 0,
            "imported": 0,
            "skipped_duplicates": 0,
            "skipped_errors": 0,
            "results": [],
        }

    # Parse source
    text = source.read_text(encoding="utf-8")
    try:
        source_entries = parse_bibtex(text)
    except Exception as exc:
        return {
            "status": "error",
            "source_path": source_path,
            "message": "failed to parse source BibTeX",
            "errors": [str(exc)],
            "total_source": 0,
            "imported": 0,
            "skipped_duplicates": 0,
            "skipped_errors": 0,
            "results": [],
        }

    if not source_entries:
        return {
            "status": "ok",
            "source_path": source_path,
            "message": "no entries found in source file",
            "errors": [],
            "total_source": 0,
            "imported": 0,
            "skipped_duplicates": 0,
            "skipped_errors": 0,
            "results": [],
        }

    # Convert to records, dedupe within source by citekey (keep first)
    records: list[dict[str, Any]] = []
    seen_citekeys: set[str] = set()
    skipped_in_source = 0

    for entry in source_entries:
        citekey = entry.get("citekey", "")
        if citekey in seen_citekeys:
            skipped_in_source += 1
            continue
        seen_citekeys.add(citekey)
        record = bibtex_entry_to_record(entry)
        record["entry_type"] = entry.get("entry_type", "article")  # type: ignore[typeddict-unknown-key]
        records.append(record)  # type: ignore[arg-type]

    # Resolve config + target once, then plan/write every record under a single
    # lock with one atomic write (see add_records_to_bib_batch) instead of
    # re-reading config and re-parsing/rewriting the whole .bib per entry.
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    results: list[dict[str, Any]] = []
    imported = 0
    skipped_dupes = 0
    skipped_errors = 0
    errors: list[str] = []

    if isinstance(resolved, list):
        # Config/target resolution failed: every record fails identically.
        batch_results: list[dict[str, Any]] = [
            {"status": "error", "citekey": r.get("citekey"),
             "message": "; ".join(resolved), "errors": resolved}
            for r in records
        ]
    else:
        config, bib = resolved
        try:
            batch_results = add_records_to_bib_batch(
                bib=bib,
                records=records,
                dry_run=dry_run,
                force_new=force_new,
                browser_hook=config.get("browser_hook", True),
                citekey_format=config.get("citekey_format"),
                pdf_filename_format=config.get("pdf_filename_format"),
                file_path_style=config.get("pdf_file_path_style", "absolute"),
            )
        except Exception as exc:
            batch_results = [
                {"status": "error", "citekey": r.get("citekey"),
                 "message": str(exc), "errors": [str(exc)]}
                for r in records
            ]

    for record, result in zip(records, batch_results):
        citekey = result.get("citekey", record.get("citekey", "?"))
        status = result.get("status", "unknown")

        if status == "ok":
            # A dedup hit against the target library comes back as an "update"
            # action (the incoming record merged into an existing entry); a new
            # entry is an "insert".  Decide on the structured action, not on
            # substring-matching the human message.
            action = result.get("action", "insert")
            if result.get("dry_run", False) or dry_run:
                results.append({
                    "citekey": citekey,
                    "status": "would_import",
                    "action": action,
                    "message": result.get("message", ""),
                })
            elif action == "update":
                skipped_dupes += 1
                results.append({
                    "citekey": citekey,
                    "status": "duplicate",
                    "message": result.get("message", ""),
                })
            else:
                imported += 1
                results.append({
                    "citekey": citekey,
                    "status": "imported",
                    "action": action,
                    "message": result.get("message", ""),
                })
        else:
            skipped_errors += 1
            results.append({
                "citekey": citekey,
                "status": "error",
                "message": result.get("message", str(result.get("errors", ""))),
            })
            errors.append(f"{citekey}: {result.get('message', 'unknown error')}")

    prefix = "DRY RUN: " if dry_run else ""
    return {
        "status": "ok",
        "source_path": source_path,
        "message": (
            f"{prefix}imported {imported}, skipped {skipped_dupes} duplicates"
            f"{', ' + str(skipped_errors) + ' errors' if skipped_errors else ''}"
        ),
        "errors": errors,
        "total_source": len(source_entries),
        "skipped_in_source": skipped_in_source,
        "imported": imported,
        "skipped_duplicates": skipped_dupes,
        "skipped_errors": skipped_errors,
        "results": results,
    }
