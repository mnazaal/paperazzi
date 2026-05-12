"""Deterministic BibTeX repository helpers."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeAlias, cast

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

from pzi.bibtex import BibtexEntry, NormalizedRecord, bibtex_entry_to_record
from pzi.service_common import _find_entry_index
from pzi.write_plan import WritePlan


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
    """Read, apply a plan, and write a BibTeX file under an exclusive lock."""
    with with_bib_lock(path):
        current = _read_bib_file_raw(path)["entries"]
        updated = apply_write_plan(current, plan)
        write_bib_file(path, updated)
        return updated


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
