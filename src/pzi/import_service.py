"""Bulk BibTeX import — import entries from a .bib file into a target library."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeAlias

from pzi.add_service import add_record_to_bib
from pzi.bib_repository import parse_bibtex
from pzi.bibtex import bibtex_entry_to_record

ImportResult: TypeAlias = dict[str, Any]


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

    # Pre-dedupe: read target bib once to check for existing matches
    # We don't have direct access to the target bib path here, but
    # add_record_to_bib handles dedupe internally. We batch calls and
    # collect results.

    results: list[dict[str, Any]] = []
    imported = 0
    skipped_dupes = 0
    skipped_errors = 0
    errors: list[str] = []

    for record in records:
        try:
            result = add_record_to_bib(
                config_path=config_path,
                home_dir=home_dir,
                record=record,
                bib_selector=bib_selector,
                dry_run=dry_run,
                force_new=force_new,
            )
        except Exception as exc:
            citekey = record.get("citekey", "?")
            results.append({
                "citekey": citekey,
                "status": "error",
                "message": str(exc),
            })
            skipped_errors += 1
            errors.append(f"{citekey}: {exc}")
            continue

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
