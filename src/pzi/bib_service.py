"""Bib administration services."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, NotRequired, TypeAlias, TypedDict

from pzi.bib_repository import (
    delete_bib_entry,
    find_entry_index,
    parse_bib_library,
    read_bib_file_raw,
    read_bib_source,
    validate_library_parseable,
    with_bib_lock,
)
from pzi.config import load_and_resolve_bib, load_config_file
from pzi.pdf_planning import pdf_file_present
from pzi.promote_service import is_preprint

BibInfo: TypeAlias = dict[str, Any]


class BibListResult(TypedDict):
    status: str
    bibs: list[BibInfo]
    errors: list[str]


def list_bibs(*, config_path: str, home_dir: str) -> BibListResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {"status": "error", "bibs": [], "errors": config_result["errors"]}
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


class DeleteEntryResult(TypedDict):
    status: str
    citekey: str
    bib_path: str
    message: str
    errors: list[str]
    dry_run: NotRequired[bool]
    title: NotRequired[str]
    pdf_path: NotRequired[str | None]
    backup_path: NotRequired[str]


def bib_stats(*, bib_path: str, papers_dir: str) -> BibStatsResult:
    """Return statistics for a BibTeX library."""
    with with_bib_lock(bib_path, shared=True):
        read_result = read_bib_file_raw(bib_path)
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
    }


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
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "message": "failed to resolve bib",
            "errors": resolved,
        }
    _config, bib = resolved
    bib_path = bib["path"]
    papers_dir = bib["papers_dir"]

    with with_bib_lock(bib_path, shared=True):
        read_result = read_bib_file_raw(bib_path)

    records = read_result["records"]
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
            key=lambda r: str(r.get("title", "")).lower(),
        )
    else:
        sorted_records = sorted(
            records,
            key=lambda r: str(r.get("citekey", "")).lower(),
        )

    page = sorted_records[offset : offset + limit]
    items = [
        {
            "citekey": str(r.get("citekey", "")),
            "title": str(r.get("title", "")),
            "year": r.get("year"),
            "authors": _author_names(r),
            "entry_type": str(r.get("entry_type", "unknown")),
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
    }


def entry_detail(
    *,
    config_path: str,
    home_dir: str,
    citekey: str,
    bib_selector: str | None = None,
) -> DetailResult:
    """Return full record detail for a single BibTeX entry by citekey."""
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "message": "failed to resolve bib",
            "errors": resolved,
            "citekey": citekey,
        }
    _config, bib = resolved

    with with_bib_lock(bib["path"], shared=True):
        read_result = read_bib_file_raw(bib["path"])

    entries = read_result["entries"]
    records = read_result["records"]
    index = find_entry_index(entries, citekey)

    if index is None:
        return {
            "status": "error",
            "citekey": citekey,
            "bib_name": bib["name"],
            "message": f"entry not found: {citekey}",
            "errors": [f"no entry with citekey {citekey}"],
        }

    record = records[index] if index < len(records) else {}
    return {
        "status": "ok",
        "citekey": citekey,
        "bib_name": bib["name"],
        "bib_path": bib["path"],
        "record": dict(record),
        "errors": [],
    }


def _author_names(record: dict[str, Any]) -> str:
    """Format author list from a record into a comma-separated string."""
    authors = record.get("authors")
    if not isinstance(authors, list) or not authors:
        return ""
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
    return "; ".join(names)


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


def _backup_path_for_delete(bib_path: str, citekey: str) -> Path:
    """Return non-existing backup path beside target bib."""
    source = Path(bib_path)
    safe_citekey = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in citekey)
    base = source.with_name(f"{source.name}.{safe_citekey}.bak")
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = source.with_name(f"{source.name}.{safe_citekey}.bak{suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


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

    backup_path = _backup_path_for_delete(bib_path, citekey)
    backup_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    shutil.copy2(bib_path, backup_path)
    delete_result = delete_bib_entry(bib_path, citekey)
    if not delete_result["found"]:
        return {
            "status": "error",
            "citekey": citekey,
            "bib_path": bib_path,
            "message": f"entry not found: {citekey}",
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
