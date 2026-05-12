"""Pure planning helpers for BibTeX persistence operations."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, cast

from pzi.bibtex import NormalizedRecord, record_to_bibtex_entry
from pzi.identity import find_exact_match
from pzi.merge import MergeableEntry, merge_entries

WriteAction = Literal["insert", "update"]


WritePlan: TypeAlias = dict[str, Any]



def plan_bib_write(
    incoming_record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    entry_type: str = "article",
) -> WritePlan:
    """Plan an insert or update operation for a normalized record."""
    match_index = find_exact_match(incoming_record, list(existing_records))
    if match_index is None:
        entry = record_to_bibtex_entry(incoming_record, entry_type=entry_type)
        return {
            "action": "insert",
            "index": None,
            "record": incoming_record,
            "entry": entry,
            "changed_fields": sorted(incoming_record.keys()),
        }

    existing_record = existing_records[match_index]
    merge_decision = merge_entries(
        cast(MergeableEntry, dict(existing_record)),
        cast(MergeableEntry, dict(incoming_record)),
    )
    merged_record = merge_decision["merged"]
    entry = record_to_bibtex_entry(merged_record, entry_type=entry_type)
    return {
        "action": "update",
        "index": match_index,
        "record": merged_record,
        "entry": entry,
        "changed_fields": merge_decision["changed_fields"],
    }
