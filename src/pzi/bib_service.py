"""Bib administration services."""

from __future__ import annotations

from typing import Any, NotRequired, TypeAlias, TypedDict

from pzi.bib_repository import (
    backup_path_for,
    delete_bib_entry,
    describe_missing_bib,
    find_entry_index,
    parse_bib_library,
    read_bib_file_raw,
    read_bib_file_with_failures,
    read_bib_source,
    validate_library_parseable,
    with_bib_lock,
)
from pzi.config import BibResolutionFailure, load_bib_target, load_config_file
from pzi.errors import REASON_CONFIG
from pzi.identifiers import is_preprint
from pzi.pdf_planning import pdf_file_present


class BibInfo(TypedDict):
    """One configured library, as `list_bibs` reports it.

    Public: `pzi.list_bibs()` returns these, so a caller can discover the names
    `library=` accepts. It was `dict[str, Any]`, which froze nothing — renaming
    a key here would have broken every caller with the snapshot still green.
    """

    #: The `[[bibs]]` name, and the value `library=` / `--target` accepts.
    name: str
    path: str
    papers_dir: str
    #: Whether this is the library used when no selector is given.
    default: bool


class BibListResult(TypedDict):
    status: str
    bibs: list[BibInfo]
    errors: list[str]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
def list_bibs(*, config_path: str, home_dir: str) -> BibListResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {
            "status": "error",
            "bibs": [],
            "errors": config_result["errors"],
            "reason": REASON_CONFIG,
        }
    config = config_result["config"]
    return {
        "status": "ok",
        "bibs": [
            {
                "name": bib["name"],
                "path": bib["path"],
                "papers_dir": bib["papers_dir"],
                "default": bib["default"],
            }
            for bib in config["bibs"]
        ],
        "errors": [],
    }


class BibStatsResult(TypedDict):
    status: str
    bib_path: str
    papers_dir: str
    total_entries: int
    with_pdf: int
    with_doi: int
    with_arxiv_id: int
    preprints: int
    entry_types: dict[str, int]
    errors: list[str]
    #: Blocks the parser dropped (e.g. a duplicate citekey). Non-fatal, so they
    #: are not `errors` — the counts above are simply of what could be read.
    warnings: NotRequired[list[str]]


class DeleteEntryResult(TypedDict):
    """One deletion — what `pzi.delete()` returns.

    `backup_path` is the pre-delete copy of the library, taken under the same
    lock as the write. `pdf_path` is the entry's PDF, which is left on disk.
    """

    status: str
    citekey: str
    bib_path: str
    message: str
    errors: list[str]
    # Structured failure kind, so callers pick an exit code without matching on
    # the message text.
    reason: NotRequired[str]
    dry_run: NotRequired[bool]
    title: NotRequired[str]
    pdf_path: NotRequired[str | None]
    backup_path: NotRequired[str]


def bib_stats(*, bib_path: str, papers_dir: str) -> BibStatsResult:
    """Return statistics for a BibTeX library."""
    read_result, dropped = read_bib_file_with_failures(bib_path)
    dropped = [*dropped, *filter(None, [describe_missing_bib(bib_path)])]
    entries = read_result["entries"]
    records = read_result["records"]

    total = len(entries)
    with_pdf = 0
    with_doi = 0
    with_arxiv = 0
    preprints = 0
    type_counts: dict[str, int] = {}

    for entry in entries:
        etype = entry.get("entry_type", "unknown")
        type_counts[etype] = type_counts.get(etype, 0) + 1

    for record in records:
        if pdf_file_present(record.get("local_pdf_path")):
            with_pdf += 1
        if record.get("doi"):
            with_doi += 1
        if record.get("arxiv_id"):
            with_arxiv += 1
        if is_preprint(record):
            preprints += 1

    return {
        "status": "ok",
        "bib_path": bib_path,
        "papers_dir": papers_dir,
        "total_entries": total,
        "with_pdf": with_pdf,
        "with_doi": with_doi,
        "with_arxiv_id": with_arxiv,
        "preprints": preprints,
        "entry_types": type_counts,
        "errors": [],
        "warnings": dropped,
    }


class EntrySummary(TypedDict):
    """One row of a library listing — what `list_entries` puts in `items`.

    Public: `pzi.entries()` and the `GET /entries` payload both return these.
    A *summary*, deliberately: it says whether a PDF is attached but not where
    it is, because a listing of 500 entries should not stat 500 files. Use
    `pzi.get(citekey)` for the full record, which carries `local_pdf_path`.
    """

    citekey: str
    #: Empty string, never None, for an entry with no title — the two other
    #: front ends render this straight into a column.
    title: str
    year: int | None
    #: `Family, Given` per author, normalized from the BibTeX `author` field.
    authors: list[str]
    #: The BibTeX entry type (`article`, `inproceedings`, …), or `unknown`.
    entry_type: str
    #: Whether the entry's `file` field points at a PDF that is actually there.
    has_pdf: bool
    doi: str | None


class EntryRecord(TypedDict):
    """A record parsed out of a `.bib`, as `pzi.get()` returns it.

    Every key is always present; absent BibTeX fields come back as None (or an
    empty list). That is a property of `bibtex.bibtex_entry_to_record`, which
    builds this from an entry's fields, and the conformance test in
    `tests/test_public_api.py` is what keeps the two in step.

    Narrower than `bibtex.NormalizedRecord` on purpose. That type is
    `total=False` and carries ~45 keys, including the `fallback_*` set that only
    exists mid-capture and the `similarity_hint`/`duplicate_of` pair that only a
    fresh insert sets. None of them can appear in a record read back from a
    file, and publishing the wide type would freeze that vocabulary as API.
    """

    citekey: str
    #: The BibTeX entry type (`article`, `inproceedings`, …), or `unknown`.
    #: Read off the parsed entry rather than the record, because
    #: `bibtex.bibtex_entry_to_record` deliberately never sets it — see
    #: `list_entries`, which does the same for `EntrySummary`.
    entry_type: str
    title: str | None
    authors: list[str]
    year: int | None
    #: `journal` or `booktitle`, whichever the entry has.
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    canonical_url: str | None
    source_url: str | None
    pdf_url: str | None
    abstract_url: str | None
    tags: list[str]
    note: str | None
    #: The path in the entry's `file` field. Not checked for existence — see
    #: `EntrySummary.has_pdf` for that question.
    local_pdf_path: str | None
    abstract: str | None
    volume: str | None
    number: str | None
    pages: str | None
    publisher: str | None
    issn: str | None
    isbn: str | None


EntriesResult: TypeAlias = dict[str, Any]
DetailResult: TypeAlias = dict[str, Any]


def list_entries(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None = None,
    offset: int = 0,
    limit: int = 50,
    sort: str = "citekey",
) -> EntriesResult:
    """List entries from a BibTeX library with pagination and sorting."""
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            # The specific reason, not "failed to resolve bib": the facade
            # surfaces `message` and only `message`, so a `pzi.entries(
            # library="nope")` said nothing about which library or which exist,
            # while every other read command named both. `commands/common.py`
            # already picks the first error this way.
            "message": (
                resolved.errors[0] if resolved.errors else "failed to resolve bib"
            ),
            "reason": REASON_CONFIG,
            "errors": resolved.errors,
        }
    _config, bib = resolved
    bib_path = bib["path"]
    papers_dir = bib["papers_dir"]

    # `_with_failures`, so a dropped block is reported rather than silently
    # shrinking `total`: a duplicate citekey keeps only the first occurrence, so
    # this used to say "1 of 1 entries" for a two-entry file.
    read_result, dropped = read_bib_file_with_failures(bib_path)
    dropped = [*dropped, *filter(None, [describe_missing_bib(bib_path)])]

    records = read_result["records"]
    # `entry_type` is a property of the BibTeX entry; `bibtex_entry_to_record`
    # deliberately never sets it, so reading it off the record reported
    # `"unknown"` for every entry, always. `--stats` and `export` have always
    # reported the real types.
    entry_types = {
        id(record): str(entry.get("entry_type") or "unknown")
        for record, entry in zip(records, read_result["entries"])
    }
    total = len(records)

    sort_field: str = sort.lower().strip()
    valid_sorts = {"citekey", "title", "year", "author"}
    if sort_field not in valid_sorts:
        sort_field = "citekey"

    if sort_field == "year":
        sorted_records = sorted(
            records,
            key=lambda r: (
                r.get("year") if isinstance(r.get("year"), int) else 0
            ),
            reverse=True,
        )
    elif sort_field == "author":
        sorted_records = sorted(
            records,
            key=lambda r: _first_author_sort_key(r).lower(),
        )
    elif sort_field == "title":
        sorted_records = sorted(
            records,
            key=lambda r: str(r.get("title") or "").lower(),
        )
    else:
        sorted_records = sorted(
            records,
            key=lambda r: str(r.get("citekey", "")).lower(),
        )

    page = sorted_records[offset : offset + limit]
    items: list[EntrySummary] = [
        {
            "citekey": str(r.get("citekey", "")),
            # `or ""`, not a `.get` default: the key is present with value
            # `None` for a titleless entry, so `str(...)` rendered the
            # literal string "None" — while `export --format json`
            # emitted a correct `null` for the same record.
            "title": str(r.get("title") or ""),
            "year": r.get("year"),
            "authors": _author_names(r),
            "entry_type": entry_types.get(id(r), "unknown"),
            "has_pdf": pdf_file_present(r.get("local_pdf_path")),
            "doi": r.get("doi"),
        }
        for r in page
    ]

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "bib_path": bib_path,
        "papers_dir": papers_dir,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort_field,
        "items": items,
        "errors": [],
        "warnings": dropped,
    }


def entry_detail(
    *,
    config_path: str,
    home_dir: str,
    citekey: str,
    bib_selector: str | None = None,
) -> DetailResult:
    """Return full record detail for a single BibTeX entry by citekey."""
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            # The specific reason, not "failed to resolve bib": the facade
            # surfaces `message` and only `message`, so a `pzi.entries(
            # library="nope")` said nothing about which library or which exist,
            # while every other read command named both. `commands/common.py`
            # already picks the first error this way.
            "message": (
                resolved.errors[0] if resolved.errors else "failed to resolve bib"
            ),
            "reason": REASON_CONFIG,
            "errors": resolved.errors,
            "citekey": citekey,
        }
    _config, bib = resolved

    read_result, dropped = read_bib_file_with_failures(bib["path"])
    dropped = [*dropped, *filter(None, [describe_missing_bib(bib["path"])])]

    entries = read_result["entries"]
    records = read_result["records"]
    index = find_entry_index(entries, citekey)

    if index is None:
        return {
            "status": "error",
            "citekey": citekey,
            "bib_name": bib["name"],
            "message": f"entry not found: {citekey}",
            "reason": "not_found",
            "errors": [f"no entry with citekey {citekey}"],
        }

    record = records[index] if index < len(records) else {}
    return {
        "status": "ok",
        "citekey": citekey,
        "bib_name": bib["name"],
        "bib_path": bib["path"],
        # `entry_type` comes from the parsed entry, not the record: without it
        # `pzi.get()` was not a superset of `pzi.entries()`, which reports it,
        # and a caller could not tell an `@article` from an `@inproceedings`
        # for an entry `export --format json` labels correctly.
        "record": {
            **record,
            "entry_type": str(entries[index].get("entry_type") or "unknown"),
        },
        "errors": [],
        "warnings": dropped,
    }


def _author_names(record: dict[str, Any]) -> list[str]:
    """Author names from a record, one per entry, as `Family, Given`.

    A list, matching `export --format json` — the entries listing used to emit a
    single joined string here, so the same field had two types depending on
    which command produced it.
    """
    authors = record.get("authors")
    if not isinstance(authors, list) or not authors:
        return []
    names = []
    for a in authors:
        if isinstance(a, str):
            if a.strip():
                names.append(a.strip())
            continue
        if not isinstance(a, dict):
            continue
        family = a.get("family", "")
        given = a.get("given", "")
        if family and given:
            names.append(f"{family}, {given}")
        elif family:
            names.append(family)
    return names


def _first_author_sort_key(record: dict[str, Any]) -> str:
    """Return stable first-author text for parsed BibTeX strings or CSL dicts."""
    authors = record.get("authors")
    if not isinstance(authors, list) or not authors:
        return ""
    first = authors[0]
    if isinstance(first, str):
        return first.strip()
    if isinstance(first, dict):
        family = first.get("family")
        given = first.get("given")
        if isinstance(family, str) and family.strip():
            return family.strip()
        if isinstance(given, str):
            return given.strip()
    return ""


def delete_entry(
    *,
    bib_path: str,
    citekey: str,
    dry_run: bool = False,
) -> DeleteEntryResult:
    """Delete a BibTeX entry by citekey, creating a backup first.

    Preserves comments, ``@string`` macros, ``@preamble`` blocks, and every
    other entry's source via :func:`delete_bib_entry` (block-level removal).
    """
    with with_bib_lock(bib_path, shared=True):
        source = read_bib_source(bib_path)
        validate_library_parseable(parse_bib_library(source))
        read_result = read_bib_file_raw(bib_path)
    entries = read_result["entries"]
    records = read_result["records"]

    index = find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "citekey": citekey,
            "bib_path": bib_path,
            "message": f"entry not found: {citekey}",
            "reason": "not_found",
            "errors": [f"no entry with citekey {citekey}"],
        }

    entry = entries[index]
    record = records[index] if index < len(records) else {}
    title = record.get("title") or entry.get("citekey", citekey)
    pdf_path = record.get("local_pdf_path")

    if dry_run:
        return {
            "status": "ok",
            "citekey": citekey,
            "bib_path": bib_path,
            "dry_run": True,
            "message": f"would delete: {title}",
            "title": title,
            "pdf_path": pdf_path,
            "errors": [],
        }

    # The copy happens inside `delete_bib_entry`'s exclusive lock, immediately
    # before the write. Doing it here left a window in which another writer
    # could rewrite the bib, making this `.bak` — which the result advertises as
    # the undo artifact — a snapshot of a version that no longer existed, so
    # restoring it would revert the other writer's work too.
    backup_path = backup_path_for(bib_path, citekey)
    delete_result = delete_bib_entry(bib_path, citekey, backup_path=backup_path)
    if not delete_result["found"]:
        return {
            "status": "error",
            "citekey": citekey,
            "bib_path": bib_path,
            "message": f"entry not found: {citekey}",
            "reason": "not_found",
            "errors": [f"no entry with citekey {citekey}"],
        }

    return {
        "status": "ok",
        "citekey": citekey,
        "bib_path": bib_path,
        "dry_run": False,
        "message": f"deleted: {title}",
        "title": title,
        "pdf_path": pdf_path,
        "backup_path": str(backup_path),
        "errors": [],
    }
