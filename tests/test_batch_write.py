"""Unit tests for the in-memory batch-write state helper.

These cover the two invariants `_apply_plan_in_memory` must preserve across a
batch: entries/records stay parallel, and the identity index never carries a
stale key after a record's identity changes mid-batch (which would otherwise
cause a false exact-match for a later record).
"""

from __future__ import annotations

from typing import Any, cast

from pzi.add_service import _apply_plan_in_memory
from pzi.bibtex import NormalizedRecord
from pzi.similarity import build_identity_index, find_exact_match


def _record(citekey: str, doi: str, title: str) -> NormalizedRecord:
    return cast(NormalizedRecord, {"citekey": citekey, "doi": doi, "title": title})


def _entry(citekey: str, doi: str, title: str) -> dict[str, Any]:
    return {
        "entry_type": "article",
        "citekey": citekey,
        "fields": {"doi": doi, "title": title},
    }


def _insert_plan(record: NormalizedRecord, entry: dict[str, Any]) -> dict[str, Any]:
    return {"action": "insert", "index": None, "record": record,
            "entry": entry, "changed_fields": []}


def _update_plan(idx: int, record: NormalizedRecord, entry: dict[str, Any]) -> dict[str, Any]:
    return {"action": "update", "index": idx, "record": record,
            "entry": entry, "changed_fields": ["doi"]}


def test_apply_plan_keeps_entries_and_records_parallel() -> None:
    records: list[NormalizedRecord] = []
    entries: list[Any] = []
    index: dict = {}

    _apply_plan_in_memory(
        entries, records, index,
        cast(Any, _insert_plan(_record("a", "10.1/a", "A"), _entry("a", "10.1/a", "A"))),
    )
    _apply_plan_in_memory(
        entries, records, index,
        cast(Any, _update_plan(0, _record("a", "10.1/a", "A2"), _entry("a", "10.1/a", "A2"))),
    )

    assert len(entries) == len(records) == 1
    assert index[("doi", "10.1/a")] == [0]


def test_update_changing_identity_drops_stale_index_key() -> None:
    # Seed one record, then update it to a different DOI. The old DOI key must
    # be removed so a later record carrying that old DOI is not falsely matched.
    records = [_record("a", "10.1/old", "A")]
    entries = [_entry("a", "10.1/old", "A")]
    index = build_identity_index(records)

    _apply_plan_in_memory(
        entries, records, index,
        cast(Any, _update_plan(0, _record("a", "10.1/new", "A"), _entry("a", "10.1/new", "A"))),
    )

    assert ("doi", "10.1/old") not in index
    assert index[("doi", "10.1/new")] == [0]

    # A new record carrying the *old* DOI must not dedup against the updated one.
    incoming = _record("c", "10.1/old", "C")
    assert find_exact_match(incoming, records, index=index) is None
