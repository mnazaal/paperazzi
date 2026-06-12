"""Bib administration services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi.bib_repository import (
    _find_entry_index,
    _parse_bib_library,
    _read_bib_file_raw,
    _read_bib_source,
    _validate_library_parseable,
    serialize_bibtex,
    with_bib_lock,
)
from pzi.config import AppConfig, BibConfig, dump_app_config, load_config_file
from pzi.promote_service import is_preprint

BibInfo: TypeAlias = dict[str, Any]


BibListResult: TypeAlias = dict[str, Any]


SetDefaultBibResult: TypeAlias = dict[str, Any]


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


def set_default_bib(
    *, config_path: str, home_dir: str, name: str
) -> SetDefaultBibResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return {
            "status": "error",
            "name": name,
            "message": "failed to load config",
            "errors": config_result["errors"],
        }
    config = config_result["config"]
    target = next((bib for bib in config["bibs"] if bib["name"] == name), None)
    if target is None:
        return {
            "status": "error",
            "name": name,
            "message": "bib not found",
            "errors": [f"no bib named {name}"],
        }

    updated_bibs = cast(
        list[BibConfig],
        [{**bib, "default": bib["name"] == name} for bib in config["bibs"]],
    )
    new_config = cast(AppConfig, {**dict(config), "bibs": updated_bibs})
    Path(config_path).write_text(dump_app_config(new_config), encoding="utf-8")
    return {
        "status": "ok",
        "name": name,
        "message": f"set default bib to {name}",
        "errors": [],
    }


BibStatsResult: TypeAlias = dict[str, Any]


DeleteEntryResult: TypeAlias = dict[str, Any]


def bib_stats(*, bib_path: str, papers_dir: str) -> BibStatsResult:
    """Return statistics for a BibTeX library."""
    with with_bib_lock(bib_path, shared=True):
        read_result = _read_bib_file_raw(bib_path)
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
        if record.get("local_pdf_path"):
            pdf_path = str(record["local_pdf_path"])
            if os.path.exists(pdf_path):
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
        read_result = _read_bib_file_raw(bib_path)

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
            key=lambda r: (
                (
                    r.get("authors", [None])[0]["family"]
                    if isinstance(r.get("authors"), list) and r.get("authors")
                    else ""
                )
            ).lower(),
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
            "has_pdf": bool(r.get("local_pdf_path")),
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
        read_result = _read_bib_file_raw(bib["path"])

    entries = read_result["entries"]
    records = read_result["records"]
    index = _find_entry_index(entries, citekey)

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
        if not isinstance(a, dict):
            continue
        family = a.get("family", "")
        given = a.get("given", "")
        if family and given:
            names.append(f"{family}, {given}")
        elif family:
            names.append(family)
    return "; ".join(names)


def delete_entry(
    *,
    bib_path: str,
    citekey: str,
    dry_run: bool = False,
) -> DeleteEntryResult:
    """Delete a BibTeX entry by citekey, creating a backup first."""
    with with_bib_lock(bib_path):
        source = _read_bib_source(bib_path)
        _validate_library_parseable(_parse_bib_library(source))
        read_result = _read_bib_file_raw(bib_path)
        entries = read_result["entries"]
        records = read_result["records"]

    index = _find_entry_index(entries, citekey)
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

    updated_entries = list(entries)
    del updated_entries[index]
    text = serialize_bibtex(updated_entries)
    bib_file = Path(bib_path)
    bib_file.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    bib_file.write_text(text, encoding="utf-8")

    return {
        "status": "ok",
        "citekey": citekey,
        "bib_path": bib_path,
        "dry_run": False,
        "message": f"deleted: {title}",
        "title": title,
        "pdf_path": pdf_path,
        "errors": [],
    }
