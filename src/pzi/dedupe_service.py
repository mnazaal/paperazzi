"""Deduplication and merge services for BibTeX libraries."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from pzi.bib_repository import (
    backup_path_for,
    merge_bib_entries,
    merge_entries,
    read_bib_file,
    read_bib_file_with_failures,
)
from pzi.bibtex import NormalizedRecord
from pzi.similarity import (
    build_identity_index,
    compute_similarity_hint,
)


class DedupeResult(TypedDict):
    status: str
    bib_path: str
    total_entries: int
    exact_duplicates: list[dict[str, Any]]
    fuzzy_candidates: list[dict[str, Any]]
    total_clusters: int
    errors: list[str]
    #: Blocks the parser dropped (e.g. a duplicate citekey). Non-fatal: the
    #: command succeeded and is reporting what it could read. Absent on the
    #: error paths, which never got as far as parsing.
    warnings: NotRequired[list[str]]


class MergeResult(TypedDict):
    status: str
    citekey_a: str
    citekey_b: str
    dry_run: bool
    message: str
    merged_title: NotRequired[str]
    dropped_citekey: NotRequired[str]
    changed_fields: NotRequired[list[str]]
    merged_record: NotRequired[dict[str, Any]]
    #: BibTeX fields of the dropped entry that the survivor takes over, and the
    #: ones it cannot (a conflict the survivor already answers differently).
    #: Both are computed from the *entries*, not from the record projection —
    #: a `NormalizedRecord` structurally cannot show `volume`/`pages`/`isbn`,
    #: which is why the dry run used to be unable to preview the loss.
    carried_fields: NotRequired[list[str]]
    dropped_fields: NotRequired[list[str]]
    #: Where the pre-merge file was copied, mirroring `delete`.
    backup_path: NotRequired[str]
    # Structured failure kind, so the runner picks an exit code without matching
    # on message text. Only "not_found" today; its absence means ENVIRONMENT.
    reason: NotRequired[str]


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
    raw, dropped = read_bib_file_with_failures(bib_path)
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
            "warnings": dropped,
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
    seen_pairs: set[frozenset[str]] = set()
    for i, record in enumerate(records):
        if i in seen_positions:
            continue
        citekey = record.get("citekey", "")
        # The record must be kept out of its own candidate corpus:
        # `compute_similarity_hint` returns only the single *best* match, and a
        # record always scores highest against itself (identical title tokens,
        # full author overlap).  Passing the whole list therefore made the
        # self-match win every time, and discarding it afterwards left the
        # fuzzy pass permanently silent.
        others = [other for j, other in enumerate(records) if j != i]
        hint = compute_similarity_hint(
            record, others,  # type: ignore[arg-type]
            title_threshold=title_threshold,
            year_window=year_window,
        )
        if not hint or hint == citekey:
            continue
        # Both members of a pair point at each other; report the pair once.
        pair = frozenset({citekey, hint})
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        fuzzy_candidates.append({
            "citekey": citekey,
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
        "warnings": dropped,
    }


def merge_duplicates(
    *,
    bib_path: str,
    citekey_a: str,
    citekey_b: str,
    dry_run: bool = True,
    file_path_style: str = "absolute",
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

    raw = read_bib_file(bib_path)
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
            "reason": "not_found",
        }
    if idx_b is None:
        return {
            "status": "error", "citekey_a": citekey_a, "citekey_b": citekey_b,
            "message": f"entry not found: {citekey_b}", "dry_run": dry_run,
            "reason": "not_found",
        }

    record_a = records[idx_a]
    record_b = records[idx_b]
    merged_title = record_b.get("title") or record_a.get("title") or citekey_b

    merge_decision = merge_entries(
        existing=dict(record_b), incoming=dict(record_a),
    )
    merged_record = merge_decision["merged"]
    changed_fields = merge_decision.get("changed_fields", [])

    # What happens to the BibTeX fields the record model does not carry. Read
    # off the entries so the preview can name them; the real merge recomputes
    # the same thing under the lock.
    fields_a = entries[idx_a].get("fields", {})
    fields_b = entries[idx_b].get("fields", {})
    carried_fields = sorted(key for key in fields_a if key not in fields_b)
    conflicting_fields = sorted(
        key for key, value in fields_a.items()
        if key in fields_b and fields_b[key] != value
    )

    if dry_run:
        return {
            "status": "ok",
            "citekey_a": citekey_a, "citekey_b": citekey_b,
            "merged_title": str(merged_title),
            "dropped_citekey": citekey_a,
            "dry_run": True,
            "message": f"would merge {citekey_a} into {citekey_b}",
            "changed_fields": changed_fields,
            "carried_fields": carried_fields,
            "dropped_fields": conflicting_fields,
            "merged_record": {
                k: v for k, v in merged_record.items() if k != "citekey"
            },
        }

    # Execute: merge A into B atomically under one lock, preserving comments,
    # @string/@preamble macros, and every other entry's source. The `.bak` is
    # written inside that lock, immediately before the write, exactly as
    # `delete` does — a merge destroys a block just as a delete does.
    backup_path = backup_path_for(bib_path, citekey_a)
    merge_result = merge_bib_entries(
        bib_path,
        citekey_a=citekey_a,
        citekey_b=citekey_b,
        file_path_style=file_path_style,
        backup_path=backup_path,
    )
    if not merge_result["found"]:
        return {
            "status": "error", "citekey_a": citekey_a, "citekey_b": citekey_b,
            "message": "entry disappeared between reads", "dry_run": dry_run,
            "reason": "not_found",
        }

    return {
        "status": "ok",
        "citekey_a": citekey_a, "citekey_b": citekey_b,
        "merged_title": str(merged_title),
        "dropped_citekey": citekey_a,
        "dry_run": False,
        "message": f"merged {citekey_a} into {citekey_b}",
        "changed_fields": changed_fields,
        "carried_fields": carried_fields,
        "dropped_fields": merge_result.get("dropped_fields", conflicting_fields),
        "backup_path": str(backup_path),
    }
