"""Deduplication and merge services for BibTeX libraries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict

from pzi.bib_repository import (
    backup_path_for,
    merge_bib_entries,
    merge_entries,
    read_bib_file,
    read_bib_file_with_notices,
)
from pzi.bibtex import NormalizedRecord
from pzi.errors import REASON_USAGE
from pzi.similarity import (
    best_fuzzy_matches,
    build_identity_index,
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
    #: The documented `--json` failure channel. Every refusal here reported
    #: `status: error` with a `message` and nothing in `errors`, so a consumer
    #: branching on the channel saw a failed command with nothing wrong.
    errors: list[str]
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
    #: The dropped entry's PDF when the merge does not keep it — left on disk
    #: with nothing referring to it, for a later `fix clean --fix` to quarantine.
    orphaned_pdf: NotRequired[str]
    #: Fields the survivor *loses*: present in both entries with different
    #: values, and resolved in the dropped entry's favour (title, venue and
    #: abstract prefer the longer string). Reported as "kept from the survivor"
    #: before, which is the opposite of what the run does.
    overwritten_fields: NotRequired[list[str]]
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
    raw, dropped = read_bib_file_with_notices(bib_path)
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
    # One cluster per *connected component* of shared identities. Reporting one
    # per index bucket repeated the same pair once for its DOI and again for its
    # arXiv id, and split a genuine three-way duplicate (A~B by DOI, B~C by URL)
    # into two overlapping clusters the user then had to reconcile by hand.
    identity_index = build_identity_index(records)
    seen_positions: set[int] = set()
    exact_duplicates: list[dict[str, Any]] = []

    for component in _identity_components(identity_index):
        citekeys = sorted({
            records[p].get("citekey", "")
            for p in component
            if p < len(records)
        })
        if len(citekeys) < 2:
            continue
        exact_duplicates.append({
            "citekeys": citekeys,
        })
        seen_positions.update(component)

    # --- Fuzzy near-duplicates ---
    # Every record must be kept out of its own candidate corpus: only the single
    # *best* match is returned, and a record always scores highest against
    # itself (identical title tokens, full author overlap), so including it left
    # the fuzzy pass permanently silent.
    #
    # Scored in one pass rather than one scan per record. Rebuilding an
    # N-element candidate list and re-tokenizing every title N times cost about
    # half an hour on a 22k-entry library, printing nothing while it ran; the
    # answers are unchanged (see `best_fuzzy_matches`).
    fuzzy_candidates: list[dict[str, Any]] = []
    seen_pairs: set[frozenset[str]] = set()
    hints = best_fuzzy_matches(
        records,  # type: ignore[arg-type]
        positions=(i for i in range(len(records)) if i not in seen_positions),
        title_threshold=title_threshold,
        year_window=year_window,
    )
    for i in sorted(hints):
        citekey = records[i].get("citekey", "")
        hint = hints[i]
        if hint == citekey:
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


def _identity_components(
    identity_index: Mapping[tuple[Any, str], list[int]],
) -> list[list[int]]:
    """Group record positions into connected components of shared identity.

    Two records are in the same component when they share *any* identity, or
    are joined transitively by a third. Returned in first-appearance order so
    the report is stable.
    """
    parent: dict[int, int] = {}

    def find(position: int) -> int:
        parent.setdefault(position, position)
        while parent[position] != position:
            parent[position] = parent[parent[position]]
            position = parent[position]
        return position

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    order: list[int] = []
    for positions in identity_index.values():
        for position in positions:
            if position not in parent:
                order.append(position)
            find(position)
        for other in positions[1:]:
            union(positions[0], other)

    components: dict[int, list[int]] = {}
    for position in order:
        components.setdefault(find(position), []).append(position)
    return [members for members in components.values() if len(members) > 1]


#: Where a record field lands in a BibTeX entry, for the few that are not
#: spelled the same. Used to say which raw fields a merge overwrites.
_RAW_KEYS_FOR_RECORD_FIELD: dict[str, tuple[str, ...]] = {
    "venue": ("journal", "booktitle"),
    "tags": ("keywords",),
    "local_pdf_path": ("file",),
    "canonical_url": ("url",),
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
            "reason": REASON_USAGE,
            "dry_run": dry_run,
            "errors": ["cannot merge an entry with itself"],
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
            "errors": [f"entry not found: {citekey_a}"],
        }
    if idx_b is None:
        return {
            "status": "error", "citekey_a": citekey_a, "citekey_b": citekey_b,
            "message": f"entry not found: {citekey_b}", "dry_run": dry_run,
            "reason": "not_found",
            "errors": [f"entry not found: {citekey_b}"],
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
    # A field present in both is only "kept from B" when the merge actually
    # keeps B's value. `merge_entries` prefers the *longer* string for title,
    # venue and abstract, so B's value is routinely replaced by A's — and this
    # list was reported as "fields kept from B (conflict)" regardless, telling
    # the user the opposite of what the run does. The dry run is where the user
    # decides, so the two outcomes are now separated by what the merge decided.
    overwritten_raw_keys = {
        raw
        for field in changed_fields
        for raw in _RAW_KEYS_FOR_RECORD_FIELD.get(field, (field,))
    }
    conflicting_fields = sorted(
        key for key, value in fields_a.items()
        if key in fields_b and fields_b[key] != value
        and key not in overwritten_raw_keys
    )
    overwritten_fields = sorted(
        key for key, value in fields_a.items()
        if key in fields_b and fields_b[key] != value
        and key in overwritten_raw_keys
    )

    # The dropped entry's PDF, when the merge does not keep it. The file stays
    # on disk with nothing referring to it, and a later `fix clean --fix`
    # quarantines it — a second command undoing what this one caused. The dry
    # run is where the user decides whether to accept that, and it reported
    # carried and dropped *fields* while never mentioning the file.
    pdf_a = record_a.get("local_pdf_path")
    kept_pdf = merged_record.get("local_pdf_path")
    orphaned_pdf = (
        str(pdf_a) if isinstance(pdf_a, str) and pdf_a and pdf_a != kept_pdf else None
    )

    if dry_run:
        preview: MergeResult = {
            "status": "ok",
            "citekey_a": citekey_a, "citekey_b": citekey_b,
            "merged_title": str(merged_title),
            "dropped_citekey": citekey_a,
            "dry_run": True,
            "message": f"would merge {citekey_a} into {citekey_b}",
            "errors": [],
            "changed_fields": changed_fields,
            "carried_fields": carried_fields,
            "dropped_fields": conflicting_fields,
            "overwritten_fields": overwritten_fields,
            "merged_record": {
                k: v for k, v in merged_record.items() if k != "citekey"
            },
        }
        if orphaned_pdf:
            preview["orphaned_pdf"] = orphaned_pdf
        return preview

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
            "errors": ["entry disappeared between reads"],
        }

    applied: MergeResult = {
        "status": "ok",
        "citekey_a": citekey_a, "citekey_b": citekey_b,
        "merged_title": str(merged_title),
        "dropped_citekey": citekey_a,
        "dry_run": False,
        "message": f"merged {citekey_a} into {citekey_b}",
        "errors": [],
        "changed_fields": changed_fields,
        "carried_fields": carried_fields,
        "dropped_fields": merge_result.get("dropped_fields", conflicting_fields),
        "backup_path": str(backup_path),
    }
    if orphaned_pdf:
        applied["orphaned_pdf"] = orphaned_pdf
    return applied
