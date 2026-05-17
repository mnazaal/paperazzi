"""Deterministic BibTeX repository helpers: I/O, locking, write planning."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

from pzi.bibtex import BibtexEntry, NormalizedRecord, bibtex_entry_to_record, record_to_bibtex_entry
from pzi.similarity import find_exact_match


def _find_entry_index(entries: Sequence[dict[str, Any]], citekey: str) -> int | None:
    """Return index of first entry with the given citekey, or None."""
    return next(
        (i for i, entry in enumerate(entries) if entry["citekey"] == citekey),
        None,
    )

# ---------------------------------------------------------------------------
# Write planning types and logic
# ---------------------------------------------------------------------------

WriteAction = Literal["insert", "update"]

WritePlan: TypeAlias = dict[str, Any]

MergeableEntry: TypeAlias = dict[str, Any]

MergeDecision: TypeAlias = dict[str, Any]


@contextmanager
def with_bib_lock(bib_path: str, shared: bool = False) -> Iterator[None]:
    """Take an advisory lock scoped to a bib file.

    Acquires an exclusive lock by default (for writes/updates).
    Pass shared=True for a shared lock (for reads).
    """
    lock_path = Path(bib_path + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    lock_type = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(fd, lock_type)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


ReadBibResult: TypeAlias = dict[str, Any]



UpdateBibEntryResult: TypeAlias = dict[str, Any]



def parse_bibtex(text: str) -> list[BibtexEntry]:
    """Parse BibTeX text into entry dictionaries using bibtexparser."""
    parser = BibTexParser(common_strings=False)
    database = bibtexparser.loads(text, parser=parser)
    return [_database_entry_to_bibtex_entry(entry) for entry in database.entries]


def serialize_bibtex(entries: list[BibtexEntry]) -> str:
    """Serialize entries in a deterministic formatting style."""
    database = BibDatabase()
    database.entries = [_bibtex_entry_to_database_entry(entry) for entry in entries]

    writer = BibTexWriter()
    writer.indent = "  "
    cast(Any, writer).order_entries_by = None
    writer.display_order = []
    return bibtexparser.dumps(database, writer)


def apply_write_plan(entries: list[BibtexEntry], plan: WritePlan) -> list[BibtexEntry]:
    """Apply an insert/update write plan to parsed BibTeX entries."""
    updated_entries = list(entries)
    if plan["action"] == "insert":
        updated_entries.append(plan["entry"])
        return updated_entries

    index = plan["index"]
    if index is None:
        raise ValueError("update plan must include an index")
        # pragma: no cover — covered by integration/browser tests
    updated_entries[index] = plan["entry"]
    return updated_entries


def read_bib_file(path: str) -> ReadBibResult:
    """Read a BibTeX file and project its entries into normalized records."""
    with with_bib_lock(path, shared=True):
        return _read_bib_file_raw(path)


def _read_bib_file_raw(path: str) -> ReadBibResult:
    """Read BibTeX file without acquiring a lock (caller must lock)."""
    file_path = Path(path)
    if not file_path.exists():
        return {"entries": [], "records": []}

    text = file_path.read_text(encoding="utf-8")
    entries = parse_bibtex(text)
    records: list[NormalizedRecord] = [bibtex_entry_to_record(entry) for entry in entries]
    return {"entries": entries, "records": records}


def write_bib_file(path: str, entries: list[BibtexEntry]) -> None:
    """Write BibTeX entries to disk in deterministic form."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(serialize_bibtex(entries), encoding="utf-8")


def execute_write_plan(path: str, plan: WritePlan) -> list[BibtexEntry]:
    """Read, apply a plan, and write a BibTeX file under an exclusive lock.

    Validates that the resulting BibTeX round-trips through
    serialize → parse before committing to disk.
    """
    with with_bib_lock(path):
        current = _read_bib_file_raw(path)["entries"]
        updated = apply_write_plan(current, plan)
        _validate_bibtex_roundtrip(updated)
        write_bib_file(path, updated)
        return updated


def _validate_bibtex_roundtrip(entries: list[BibtexEntry]) -> None:
    """Raise ValueError if entries cannot survive a serialize→parse round-trip."""
    try:
        text = serialize_bibtex(entries)
        parse_bibtex(text)
    except Exception as exc:
        raise ValueError(
            f"write plan produces invalid BibTeX: {exc}"
        ) from exc


def update_bib_entry(
    path: str,
    citekey: str,
    updater: Callable[[BibtexEntry, NormalizedRecord], BibtexEntry],
) -> UpdateBibEntryResult:
    """Update one BibTeX entry under lock using a citekey-scoped callback."""
    with with_bib_lock(path):
        read_result = _read_bib_file_raw(path)
        entries = list(read_result["entries"])
        records = read_result["records"]
        index = _find_entry_index(entries, citekey)
        if index is None:
            return {"found": False, "entries": entries, "entry": None, "record": None}

        current_entry = entries[index]
        current_record = records[index]
        updated_entry = updater(current_entry, current_record)
        if updated_entry != current_entry:
            entries[index] = updated_entry
            write_bib_file(path, entries)
        return {
            "found": True,
            "entries": entries,
            "entry": updated_entry,
            "record": current_record,
        }


def _database_entry_to_bibtex_entry(entry: dict[str, str]) -> BibtexEntry:
    fields = {
        key.lower(): value
        for key, value in entry.items()
        if key not in {"ENTRYTYPE", "ID"}
    }
    return {
        "entry_type": entry["ENTRYTYPE"],
        "citekey": entry["ID"],
        "fields": dict(sorted(fields.items())),
    }


def _bibtex_entry_to_database_entry(entry: BibtexEntry) -> dict[str, str]:
    return {
        "ENTRYTYPE": entry["entry_type"],
        "ID": entry["citekey"],
        **dict(sorted(entry["fields"].items())),
    }


# ---------------------------------------------------------------------------
# Write planning
# ---------------------------------------------------------------------------

_USER_OWNED_FIELDS = frozenset({"tags", "citekey"})
_PREFER_LONGER_TEXT_FIELDS = frozenset({"title", "venue", "note", "abstract"})
_FILL_IF_MISSING_FIELDS = frozenset(
    {
        "doi",
        "arxiv_id",
        "canonical_url",
        "source_url",
        "pdf_url",
        "abstract_url",
        "local_pdf_path",
        "source_name",
        "source_payload",
    }
)


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


def merge_entries(existing: MergeableEntry, incoming: MergeableEntry) -> MergeDecision:
    """Merge an incoming record into an existing entry conservatively."""
    merged = cast(MergeableEntry, dict(existing))
    changed_fields: list[str] = []

    existing_tags = existing.get("tags") or []
    incoming_tags = incoming.get("tags") or []
    merged_tags = sorted({*existing_tags, *incoming_tags})
    if merged_tags != existing_tags:
        merged["tags"] = merged_tags
        changed_fields.append("tags")
    elif existing.get("tags") is not None:
        merged["tags"] = existing["tags"]

    existing_authors = existing.get("authors") or []
    incoming_authors = incoming.get("authors") or []
    merged_authors = (
        incoming_authors
        if len(incoming_authors) > len(existing_authors)
        else existing_authors
    )
    if merged_authors != existing_authors:
        merged["authors"] = merged_authors
        changed_fields.append("authors")

    existing_year = existing.get("year")
    incoming_year = incoming.get("year")
    merged_year = existing_year if existing_year is not None else incoming_year
    if merged_year != existing_year:
        merged["year"] = merged_year
        changed_fields.append("year")

    for field in _PREFER_LONGER_TEXT_FIELDS:
        current_value = existing.get(field)
        incoming_value = incoming.get(field)
        merged_value = _prefer_more_informative_text(current_value, incoming_value)
        if merged_value != current_value:
            merged[field] = merged_value
            changed_fields.append(field)

    for field in _FILL_IF_MISSING_FIELDS:
        current_value = existing.get(field)
        incoming_value = incoming.get(field)
        has_current = False
        if current_value is not None:
            if isinstance(current_value, str):
                has_current = bool(current_value.strip())
            elif isinstance(current_value, list):
                has_current = bool(current_value)
            else:
                has_current = True
        merged_value = current_value if has_current else incoming_value
        if merged_value != current_value:
            merged[field] = merged_value
            changed_fields.append(field)

    for field in _USER_OWNED_FIELDS:
        if field not in merged and field in existing:
            merged[field] = existing[field]

    return {
        "merged": merged,
        "changed_fields": sorted(set(changed_fields)),
    }


def _prefer_more_informative_text(
    existing: str | None, incoming: str | None,
) -> str | None:
    if existing is None or (isinstance(existing, str) and not existing.strip()):
        return incoming
    if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
        return existing
    assert isinstance(existing, str)
    assert isinstance(incoming, str)
    return incoming if len(incoming.strip()) > len(existing.strip()) else existing
