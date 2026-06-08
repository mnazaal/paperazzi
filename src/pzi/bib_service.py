"""Bib administration services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi.bib_repository import (
    _find_entry_index,
    _read_bib_file_raw,
    _read_bib_source,
    _validate_source_patchable,
    create_bib_backup,
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


def delete_entry(
    *,
    bib_path: str,
    citekey: str,
    dry_run: bool = False,
) -> DeleteEntryResult:
    """Delete a BibTeX entry by citekey, creating a backup first."""
    with with_bib_lock(bib_path):
        source = _read_bib_source(bib_path)
        _validate_source_patchable(source)
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

    backup_path = create_bib_backup(bib_path)
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
        "backup_path": backup_path,
        "errors": [],
    }
