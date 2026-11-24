"""Bulk BibTeX import — import entries from a .bib file into a target library."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, TypedDict

from pzi.add_planning import batch_status
from pzi.add_service import add_records_to_bib_batch
from pzi.bib_repository import read_bib_file
from pzi.bib_serialize import parse_bibtex_for_import, resolved_attachment_paths
from pzi.bibtex import bibtex_entry_to_record
from pzi.config import DEFAULT_PDF_FILE_PATH_STYLE, BibResolutionFailure, load_bib_target
from pzi.errors import REASON_CONFIG, REASON_UNAVAILABLE, REASON_USAGE, PziError
from pzi.fileio import read_text_utf8


class ImportResult(TypedDict):
    status: str
    source_path: str
    #: The library imported into. `NotRequired` because the failures before
    #: target resolution do not know it yet. The runner cannot supply it —
    #: unlike the other five commands, `import` passes a selector straight
    #: through and never resolves a target of its own — so the service reports
    #: it, and `import --json` stops saying `"bib_name": null` forever.
    bib_name: NotRequired[str]
    message: str
    errors: list[str]
    #: The writer's warnings — a near-duplicate insert above all. `NotRequired`
    #: because the early failure returns have none to report; consumers read it
    #: with `.get`.
    warnings: NotRequired[list[str]]
    total_source: int
    imported: int
    #: Existing entries an incoming record was merged into.
    updated: NotRequired[int]
    skipped_duplicates: int
    skipped_errors: int
    results: list[dict[str, Any]]
    skipped_in_source: NotRequired[int]
    #: Whether this was a preview. `update` and `add` both carry it; without it
    #: a dry run's `"imported": 0` was indistinguishable from a real run that
    #: imported nothing.
    dry_run: bool
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]


def import_from_bibtex(
    *,
    config_path: str,
    home_dir: str,
    source_path: str,
    source_text: str | None = None,
    bib_selector: str | None = None,
    dry_run: bool = False,
    force_new: bool = False,
) -> ImportResult:
    """Import entries from a BibTeX file into the configured target library.

    Pass *source_text* to import already-read BibTeX (the CLI does this for
    ``pzi import -``, reading stdin); *source_path* is then only a label for
    messages. Deduplicates against the target library using DOI/arXiv ID/URL
    matching. Returns a dict with import status, per-entry results, and summary
    counts.
    """
    source = Path(source_path)
    if source_text is None and not source.exists():
        return {
            "status": "error",
            "source_path": source_path,
            "dry_run": dry_run,
            "message": "source file not found",
            "reason": REASON_USAGE,
            "errors": [f"file not found: {source_path}"],
            "total_source": 0,
            "imported": 0,
            "skipped_duplicates": 0,
            "skipped_errors": 0,
            "results": [],
        }

    # Parse source
    text = source_text if source_text is not None else read_text_utf8(source)
    # bibtexparser v2 does not raise on a malformed block — it collects it and
    # returns the rest, so the old `try/except` here could never fire and every
    # unreadable entry vanished from an import that reported success. Import the
    # blocks that parsed and account for the ones that did not.
    source_entries, dropped_blocks = parse_bibtex_for_import(text)

    if not source_entries and dropped_blocks:
        return {
            "status": "error",
            "source_path": source_path,
            "dry_run": dry_run,
            "message": "failed to parse source BibTeX",
            "reason": REASON_USAGE,
            "errors": dropped_blocks,
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
            "dry_run": dry_run,
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
    # The entry each record was read from, positionally aligned with `records`.
    # Carried through to the write so the fields `NormalizedRecord` does not
    # model — `volume`, `pages`, `publisher`, `editor`, `series`, `isbn`,
    # `crossref` — are copied into the library rather than projected away.
    record_source_entries: list[Any] = []
    seen_citekeys: set[str] = set()
    skipped_in_source = 0

    for entry in source_entries:
        citekey = entry.get("citekey", "")
        if citekey in seen_citekeys:
            skipped_in_source += 1
            continue
        seen_citekeys.add(citekey)
        record = bibtex_entry_to_record(entry)
        record["entry_type"] = entry.get("entry_type", "article")
        records.append(record)  # type: ignore[arg-type]
        record_source_entries.append(entry)

    # Resolve config + target once, then plan/write every record under a single
    # lock with one atomic write (see add_records_to_bib_batch) instead of
    # re-reading config and re-parsing/rewriting the whole .bib per entry.
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )

    results: list[dict[str, Any]] = []
    imported = 0
    updated = 0
    skipped_dupes = 0
    # A block the parser could not read is a source entry that did not make it
    # in, which is what `skipped_errors` counts — and what makes the command
    # exit PARTIAL rather than claiming a clean import.
    skipped_errors = len(dropped_blocks)
    errors: list[str] = list(dropped_blocks)
    warnings: list[str] = []

    if isinstance(resolved, BibResolutionFailure):
        # Nothing ran: an unloadable config or an unknown `--target` is not a
        # batch that partly failed. Fanning it out into one error per record
        # made the run report `status: "ok"` with N errors for one root cause,
        # and the runner mapped that to PARTIAL — "some items failed" when no
        # item was ever attempted. Report the run itself as failed; the runner
        # already maps that to ENVIRONMENT.
        return {
            "status": "error",
            "source_path": source_path,
            "dry_run": dry_run,
            "message": "failed to resolve target library",
            "reason": REASON_CONFIG,
            "errors": resolved.errors,
            "total_source": len(source_entries) + len(dropped_blocks),
            "imported": 0,
            "skipped_duplicates": 0,
            "skipped_errors": 0,
            "results": [],
        }

    config, bib = resolved
    try:
        batch_results = add_records_to_bib_batch(
            bib=bib,
            records=records,
            source_entries=record_source_entries,
            dry_run=dry_run,
            force_new=force_new,
            browser_hook=config.get("browser_hook", True),
            citekey_format=config.get("citekey_format"),
            pdf_filename_format=config.get("pdf_filename_format"),
            file_path_style=config.get("pdf_file_path_style", DEFAULT_PDF_FILE_PATH_STYLE),
        )
    except Exception as exc:  # reported, not swallowed
        # The batch write is transactional: if it raised, nothing was written.
        # Reporting one error per record made that look like a partly-failed
        # batch (PARTIAL) when in fact no record was imported, so this is a
        # run failure like the resolution failure above.
        return {
            "status": "error",
            "source_path": source_path,
            "bib_name": bib["name"],
            "dry_run": dry_run,
            "message": "failed to write imported records",
            # Every record already passed resolution/validation above; the
            # write itself (a filesystem operation) broke. That's an external
            # dependency failing after a good request, not a bad one — retrying
            # once the underlying issue clears is reasonable, unlike usage/config.
            "reason": REASON_UNAVAILABLE,
            "errors": [str(exc)],
            "total_source": len(source_entries) + len(dropped_blocks),
            "imported": 0,
            "skipped_duplicates": 0,
            "skipped_errors": 0,
            "results": [],
        }

    #: Source entries merged into an existing entry, checked after the loop for
    #: attachments the entry's own `file` field did not take.
    merged_sources: list[tuple[str | None, Any]] = []
    for record, source_entry, result in zip(records, record_source_entries, batch_results):
        citekey = result.get("citekey", record.get("citekey", "?"))
        status = result.get("status", "unknown")
        # The writer reports a near-duplicate as a warning, not an error, and
        # this module never read them — the string `warnings` did not occur in
        # it. So re-importing a file inserted `good1-2` beside `good1` and said
        # nothing, and the library quietly doubled.
        for warning in result.get("warnings") or []:
            warnings.append(f"{citekey}: {warning}")

        if status == "ok":
            # A dedup hit against the target library comes back as an "update"
            # action (the incoming record merged into an existing entry); a new
            # entry is an "insert".  Decide on the structured action, not on
            # substring-matching the human message.
            action = result.get("action", "insert")
            # A preview classifies exactly as the run it previews. It used to
            # count every ok result as an import, so `--dry-run` promised
            # "imported 2/2" for a run that then said "imported 1/2, skipped 1
            # duplicates" — the one number a preview exists to get right, wrong
            # in the direction that makes the user proceed.
            previewing = bool(result.get("dry_run", False) or dry_run)
            if action == "update":
                merged_sources.append((citekey, source_entry))
            if action == "update" and not result.get("changed_fields"):
                # Merged into an existing entry and changed nothing about it —
                # a re-import of a record the library already has.
                skipped_dupes += 1
                results.append({
                    "citekey": citekey,
                    "status": "duplicate",
                    "message": result.get("message", ""),
                })
            elif action == "update":
                # `update` means the incoming record was merged into an existing
                # entry; `changed_fields` says whether that merge changed
                # anything. Both were reported as a skipped duplicate, so a run
                # that had just given an entry an abstract said "imported 0/1,
                # skipped 1 duplicate" — nothing happened, according to the
                # summary, while the library disagreed. A re-import of the same
                # record changes nothing and is still a duplicate.
                updated += 1
                results.append({
                    "citekey": citekey,
                    "status": "would_update" if previewing else "updated",
                    "action": action,
                    "changed_fields": list(result.get("changed_fields") or []),
                    "message": result.get("message", ""),
                })
            else:
                imported += 1
                results.append({
                    "citekey": citekey,
                    "status": "would_import" if previewing else "imported",
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

    try:
        warnings.extend(
            _unmerged_attachment_warnings(bib["path"], source_path, merged_sources)
        )
    except (PziError, OSError, ValueError, RuntimeError) as exc:
        # Reported, not raised: the import already committed. A second writer
        # holding the lock past its timeout (PziError), an unreadable file
        # (OSError), or `~unknownuser/` in a `file` field (RuntimeError from
        # `expanduser`) must not turn a committed import into a failure.
        warnings.append(
            "could not re-read the library to check for source attachments a "
            f"merge left out: {exc}"
        )

    prefix = "DRY RUN: " if dry_run else ""
    return {
        # Derived, not hardcoded — see `batch_status`. A skipped duplicate
        # counts as succeeded: the importer found the paper already present,
        # which is an answer, not a failure.
        "status": batch_status(
            succeeded=imported + updated + skipped_dupes, failed=skipped_errors,
        ),
        "warnings": warnings,
        "source_path": source_path,
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "message": (
            f"{prefix}imported {imported}"
            f"{', updated ' + str(updated) if updated else ''}"
            f", skipped {skipped_dupes} duplicates"
            f"{', ' + str(skipped_errors) + ' errors' if skipped_errors else ''}"
        ),
        "errors": errors,
        # Count the unreadable blocks too: `imported N/total` must not compare
        # against a total that already quietly excluded them.
        "total_source": len(source_entries) + len(dropped_blocks),
        "skipped_in_source": skipped_in_source,
        "imported": imported,
        # Entries an incoming record was merged *into*. Counted separately from
        # `skipped_duplicates`, which means "this record changed nothing".
        "updated": updated,
        "skipped_duplicates": skipped_dupes,
        "skipped_errors": skipped_errors,
        "results": results,
    }


def _unmerged_attachment_warnings(
    bib_path: str, source_path: str, merged_sources: list[tuple[str | None, Any]]
) -> list[str]:
    """Say which source attachments a merge left out, instead of dropping them silently.

    An incoming record merged into an entry that already names a file keeps the
    entry's own `file` field: pzi does not re-compose one field out of two
    producers' syntaxes (bare `;` lists, Zotero/JabRef composites). The source's
    other attachments were therefore lost with nothing said — the run reported
    a skipped duplicate. Compared by resolved location, so a `~/` or relative
    spelling of the same file is not reported.

    Known limit: it reads the library after the batch, so a dry run cannot see
    an entry inserted earlier in the same batch and stays silent where the real
    run warns. Building the warning from the batch's own plans fixes that; it
    needs the batch writer (`add_service`), so it is queued with the import
    lane rather than done here.
    """
    if not merged_sources:
        return []
    entries = read_bib_file(bib_path)["entries"]
    by_key = {entry.get("citekey"): entry for entry in entries}

    def located(paths: list[str]) -> set[str]:
        return {str(Path(path).expanduser().resolve()) for path in paths}

    warnings: list[str] = []
    for citekey, source_entry in merged_sources:
        entry = by_key.get(citekey)
        if entry is None or not (entry["fields"].get("file") or "").strip():
            continue
        kept = located(resolved_attachment_paths(entry, bib_path))
        offered = resolved_attachment_paths(source_entry, source_path)
        left_out = [path for path in offered if located([path]).isdisjoint(kept)]
        if left_out:
            warnings.append(
                f"{citekey}: kept the library entry's own file field; not merged "
                f"from the source: {', '.join(left_out)}"
            )
    return warnings
