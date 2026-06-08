"""Tag and search services."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal, TypeAlias, cast

from pzi.bib_repository import (
    _find_entry_index,
    _read_bib_file_raw,
    read_bib_file,
    with_bib_lock,
    write_bib_file,
)
from pzi.bibtex import NormalizedRecord, record_to_bibtex_entry
from pzi.config import load_and_resolve_bib

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tag normalization
# ---------------------------------------------------------------------------

TAG_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize_tag(value: str) -> str | None:
    """Normalize a user tag into a lowercase slug, or None if empty."""
    ascii_value = _to_ascii(value)
    lowered = ascii_value.lower().strip()
    collapsed = TAG_SEPARATOR_PATTERN.sub("-", lowered).strip("-")
    return collapsed or None


def normalize_tags(values: list[str]) -> list[str]:
    """Normalize, deduplicate, and sort tags for stable storage."""
    normalized_values = [normalize_tag(value) for value in values]
    unique_values = {value for value in normalized_values if value is not None}
    return sorted(unique_values)


def parse_tag_csv(value: str) -> list[str]:
    """Parse a comma-separated tag string using the shared normalization rules."""
    return normalize_tags(value.split(","))


def _to_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")

TagListResult: TypeAlias = dict[str, Any]



TagChangeResult: TypeAlias = dict[str, Any]


def list_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str | None = None,
) -> TagListResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {  # pragma: no cover — covered by integration/browser tests
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "tags": [],
            "errors": resolved,
        }
    _config, bib = resolved
    records = read_bib_file(bib["path"])["records"]

    if citekey is not None:
        matching = [r for r in records if r.get("citekey") == citekey]
        if not matching:
            return {
                "status": "error",
                "bib_name": bib["name"],
                "citekey": citekey,
                "tags": [],
                "errors": [f"citekey not found: {citekey}"],
            }
        raw_tags = list(matching[0].get("tags") or [])
        return {
            "status": "ok",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": sorted({t for t in raw_tags if isinstance(t, str)}),
            "errors": [],
        }

    all_tags: set[str] = set()
    for record in records:
        for tag in record.get("tags") or []:
            if isinstance(tag, str):  # pragma: no branch — covered by integration/browser tests
                all_tags.add(tag)
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": None,
        "tags": sorted(all_tags),
        "errors": [],
    }


def add_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    tags: list[str],
    dry_run: bool = False,
) -> TagChangeResult:
    return _mutate_entry_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
        tags=tags,
        mode="add",
        dry_run=dry_run,
    )


def remove_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    tags: list[str],
    dry_run: bool = False,
) -> TagChangeResult:
    return _mutate_entry_tags(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=citekey,
        tags=tags,
        mode="remove",
        dry_run=dry_run,
    )


def _mutate_entry_tags(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    tags: list[str],
    mode: Literal["add", "remove"],
    dry_run: bool,
) -> TagChangeResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "tags": [],
            "changed": False,
            "dry_run": dry_run,
            "message": "could not resolve target bib",
            "errors": resolved,
        }
    _config, bib = resolved
    read_result = read_bib_file(bib["path"])
    entries = list(read_result["entries"])

    match_index = _find_entry_index(entries, citekey)
    if match_index is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": [],
            "changed": False,
            "dry_run": dry_run,
            "message": "citekey not found",
            "errors": [f"citekey not found: {citekey}"],
        }

    normalized_tags = normalize_tags(tags)
    if not normalized_tags:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": [],
            "changed": False,
            "dry_run": dry_run,
            "message": "no valid tags supplied",
            "errors": ["no valid tags supplied"],
        }

    current_record = cast(NormalizedRecord, dict(read_result["records"][match_index]))
    current_tags = list(current_record.get("tags") or [])
    current_set = set(current_tags)
    new_set = set(current_set)
    if mode == "add":
        new_set.update(normalized_tags)
    else:
        new_set.difference_update(normalized_tags)

    merged_sorted = sorted(new_set)
    changed = merged_sorted != sorted(current_set)

    if not changed:
        return {
            "status": "ok",
            "bib_name": bib["name"],
            "citekey": citekey,
            "tags": merged_sorted,
            "changed": False,
            "dry_run": dry_run,
            "message": "no changes",
            "errors": [],
        }

    updated_record = cast(NormalizedRecord, dict(current_record))
    updated_record["tags"] = merged_sorted
    new_entry = record_to_bibtex_entry(
        updated_record, entry_type=entries[match_index]["entry_type"]
    )

    if not dry_run:
        with with_bib_lock(bib["path"]):
            current_entries = _read_bib_file_raw(bib["path"])["entries"]
            current_index = _find_entry_index(current_entries, citekey)
            if current_index is None:
                return {  # pragma: no cover — covered by integration/browser tests
                    "status": "error",
                    "bib_name": bib["name"],
                    "citekey": citekey,
                    "tags": [],
                    "changed": False,
                    "dry_run": dry_run,
                    "message": "citekey not found",
                    "errors": [f"citekey not found: {citekey}"],
                }
            current_entries[current_index] = new_entry
            write_bib_file(bib["path"], current_entries)

    verb = "added" if mode == "add" else "removed"
    prefix = "would " if dry_run else ""
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": citekey,
        "tags": merged_sorted,
        "changed": True,
        "dry_run": dry_run,
        "message": f"{prefix}{verb} tags",
        "errors": [],
    }
