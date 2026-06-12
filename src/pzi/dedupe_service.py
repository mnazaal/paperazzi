"""Deduplication and merge services for BibTeX libraries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeAlias

from pzi.bib_repository import (
    _read_bib_file_raw,
    merge_entries,
    serialize_bibtex,
    with_bib_lock,
)
from pzi.bibtex import NormalizedRecord, record_to_bibtex_entry
from pzi.similarity import (
    build_identity_index,
    compute_similarity_hint,
)

DedupeResult: TypeAlias = dict[str, Any]
MergeResult: TypeAlias = dict[str, Any]


def find_duplicates(
    *,
    bib_path: str,
    title_threshold: float = 0.6,
    year_window: int = 2,
) -> DedupeResult:
    """Find duplicate entries in a BibTeX library.

    Returns exact matches (by DOI / arXiv ID / canonical URL) and
    fuzzy near-matches (by title similarity + author overlap + year).

    Returns:
        dict with ``status``, ``exact_duplicates`` (list of citekey pairs),
        ``fuzzy_candidates`` (list of citekey + hint dicts), and counts.
    """
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
    records: list[NormalizedRecord] = raw["records"]

    if not records:
        return {
            "status": "ok",
            "bib_path": bib_path,
            "total_entries": 0,
            "exact_duplicates": [],
            "fuzzy_candidates": [],
            "total_clusters": 0,
            "errors": [],
        }

    # --- Exact duplicates via identity index ---
    identity_index = build_identity_index(records)
    seen_positions: set[int] = set()
    exact_duplicates: list[dict[str, Any]] = []

    for positions in sorted(identity_index.values(), key=min):
        if len(positions) < 2:
            continue
        citekeys = sorted({
            records[p].get("citekey", "")
            for p in positions
            if p < len(records)
        })
        if len(citekeys) < 2:
            continue
        exact_duplicates.append({
            "citekeys": citekeys,
        })
        seen_positions.update(positions)

    # --- Fuzzy near-duplicates ---
    fuzzy_candidates: list[dict[str, Any]] = []
    for i, record in enumerate(records):
        if i in seen_positions:
            continue
        hint = compute_similarity_hint(
            record, records,
            title_threshold=title_threshold,
            year_window=year_window,
        )
        if hint and hint != record.get("citekey"):
            fuzzy_candidates.append({
                "citekey": record.get("citekey", ""),
                "hint": hint,
            })

    return {
        "status": "ok",
        "bib_path": bib_path,
        "total_entries": len(records),
        "exact_duplicates": exact_duplicates,
        "fuzzy_candidates": fuzzy_candidates,
        "total_clusters": len(exact_duplicates),
        "errors": [],
    }


def merge_duplicates(
    *,
    bib_path: str,
    citekey_a: str,
    citekey_b: str,
    dry_run: bool = True,
) -> MergeResult:
    """Merge two entries in a BibTeX library by citekey.

    Merges ``citekey_a`` into ``citekey_b`` (a → b), keeping b's citekey.
    Uses :func:`merge_entries` for conservative field merging.

    Returns:
        dict with ``status``, ``citekey_a``, ``citekey_b``,
        ``merged_title``, ``dropped_citekey``, and ``dry_run``.
    """
    if citekey_a == citekey_b:
        return {
            "status": "error",
            "citekey_a": citekey_a,
            "citekey_b": citekey_b,
            "message": "cannot merge an entry with itself",
            "dry_run": dry_run,
        }

    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
    entries = raw["entries"]
    records = raw["records"]

    # Locate both entries
    idx_a = next(
        (i for i, e in enumerate(entries) if e["citekey"] == citekey_a), None
    )
    idx_b = next(
        (i for i, e in enumerate(entries) if e["citekey"] == citekey_b), None
    )

    if idx_a is None:
        return {
            "status": "error", "citekey_a": citekey_a, "citekey_b": citekey_b,
            "message": f"entry not found: {citekey_a}", "dry_run": dry_run,
        }
    if idx_b is None:
        return {
            "status": "error", "citekey_a": citekey_a, "citekey_b": citekey_b,
            "message": f"entry not found: {citekey_b}", "dry_run": dry_run,
        }

    record_a = records[idx_a]
    record_b = records[idx_b]
    merged_title = record_b.get("title") or record_a.get("title") or citekey_b

    merge_decision = merge_entries(
        existing=dict(record_b), incoming=dict(record_a),
    )
    merged_record = merge_decision["merged"]
    changed_fields = merge_decision.get("changed_fields", [])

    if dry_run:
        return {
            "status": "ok",
            "citekey_a": citekey_a, "citekey_b": citekey_b,
            "merged_title": str(merged_title),
            "dropped_citekey": citekey_a,
            "dry_run": True,
            "message": f"would merge {citekey_a} into {citekey_b}",
            "changed_fields": changed_fields,
            "merged_record": {
                k: v for k, v in merged_record.items() if k != "citekey"
            },
        }

    # Execute: re-read, merge, delete a, update b
    with with_bib_lock(bib_path):  # exclusive
        fresh_raw = _read_bib_file_raw(bib_path)
        fresh_entries = fresh_raw["entries"]
        fresh_records = fresh_raw["records"]

        idx_a_fresh = next(
            (i for i, e in enumerate(fresh_entries) if e["citekey"] == citekey_a),
            None,
        )
        idx_b_fresh = next(
            (i for i, e in enumerate(fresh_entries) if e["citekey"] == citekey_b),
            None,
        )
        if idx_a_fresh is None or idx_b_fresh is None:
            return {
                "status": "error", "citekey_a": citekey_a, "citekey_b": citekey_b,
                "message": "entry disappeared between reads", "dry_run": dry_run,
            }

        # Merge on fresh records
        decision = merge_entries(
            existing=dict(fresh_records[idx_b_fresh]),
            incoming=dict(fresh_records[idx_a_fresh]),
        )
        merged = decision["merged"]

        # Convert merged record to BibTeX entry
        entry_b_merged = record_to_bibtex_entry(
            merged,
            entry_type=fresh_entries[idx_b_fresh].get("entry_type", "article"),
        )

        # Build new entry list: drop a, replace b with merged
        updated = [e for i, e in enumerate(fresh_entries) if i != idx_a_fresh]
        b_pos = idx_b_fresh if idx_a_fresh > idx_b_fresh else idx_b_fresh - 1
        updated[b_pos] = entry_b_merged

        text = serialize_bibtex(updated)
        Path(bib_path).write_text(text, encoding="utf-8")

    return {
        "status": "ok",
        "citekey_a": citekey_a, "citekey_b": citekey_b,
        "merged_title": str(merged_title),
        "dropped_citekey": citekey_a,
        "dry_run": False,
        "message": f"merged {citekey_a} into {citekey_b}",
    }
