"""Deterministic BibTeX repository helpers: I/O, locking, write planning."""

from __future__ import annotations

import difflib
import fcntl
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter

from pzi.bib_preserve import (
    append_entry_preserving_source,
    bibtex_source_errors,
    patch_entry_fields_preserving_source,
)
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    bibtex_entry_to_record,
    record_to_bibtex_entry,
    resolve_citekey_collision,
)
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

    Uses fcntl.flock, which is reliable on local Linux/macOS filesystems
    but may not serialize access correctly on NFS or other network
    filesystems. The BibTeX library should be stored on a local disk.
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
    for record, entry in zip(records, entries):
        _resolve_file_field(record, entry, path)
    return {"entries": entries, "records": records}


def _resolve_file_field(record: NormalizedRecord, entry: BibtexEntry, bib_path: str) -> None:
    """Resolve a relative ``file`` field to an absolute ``local_pdf_path``.

    When a BibTeX entry stores ``file = {papers/citekey.pdf}`` (relative
    to the bib file location), this helper resolves it to an absolute path
    so that internal consumers (PDF open, status checks) can locate the
    file without knowing the bib directory.

    Absolute paths and home-relative paths (``~/...``) are kept as-is.
    """
    raw = entry.get("fields", {}).get("file")
    if not raw:
        return
    value = str(raw).strip()
    if not value:
        return
    # Already absolute or home-relative — leave as stored in record.
    if value.startswith(("/", "~")):
        record.setdefault("local_pdf_path", value)
        return
    # Best-effort relative resolution: <bib-dir>/<file-value>.
    bib_dir = str(Path(bib_path).parent)
    record["local_pdf_path"] = str(Path(bib_dir) / value)


def _normalize_file_field(entry: BibtexEntry, bib_path: str) -> BibtexEntry:
    """Normalise an absolute ``file`` field to a relative path.

    Paths under the bib file directory are shortened (e.g.
    ``/home/alice/bibs/papers/x.pdf`` → ``papers/x.pdf``).
    Paths outside the bib directory, already-relative paths, and
    home-relative paths (``~/...``) are kept as-is.
    """
    raw = entry.get("fields", {}).get("file")
    if not raw:
        return entry
    value = str(raw).strip()
    if not value or not value.startswith("/"):
        return entry  # already relative, home-relative, or non-path
    bib_dir = str(Path(bib_path).parent)
    file_path = Path(value)
    try:
        rel = str(file_path.resolve().relative_to(Path(bib_dir).resolve()))
    except ValueError:
        return entry  # not under bib dir — keep absolute
    new_entry: BibtexEntry = dict(entry)  # type: ignore[assignment]
    new_entry["fields"] = dict(entry["fields"])
    new_entry["fields"]["file"] = rel
    return new_entry


def write_bib_file(path: str, entries: list[BibtexEntry]) -> None:
    """Write BibTeX entries to disk atomically.

    Before writing, absolute ``file`` paths are normalised to paths
    relative to the bib file directory (e.g. ``papers/citekey.pdf``)
    so that the resulting ``.bib`` file is portable across machines
    and sync-friendly.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    entries = [_normalize_file_field(entry, path) for entry in entries]
    content = serialize_bibtex(entries).encode("utf-8")
    # Atomic write: temp file in same directory, then os.replace.
    # os.replace is atomic on POSIX (rename) and on Windows (MoveFileEx
    # with MOVEFILE_REPLACE_EXISTING) when src and dst are on the same
    # filesystem.
    fd, tmp = tempfile.mkstemp(dir=str(file_path.parent), prefix=".bib-", suffix=".tmp")
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    os.replace(tmp, file_path)


def _write_bib_text_atomic(path: str, text: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    content = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(file_path.parent), prefix=".bib-", suffix=".tmp")
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    os.replace(tmp, file_path)


def _read_bib_source(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def create_bib_backup(path: str) -> str:
    """Create a same-directory backup of current BibTeX source."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, backup_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f"{file_path.name}.",
        suffix=".bak",
    )
    os.close(fd)
    if file_path.exists():
        shutil.copyfile(file_path, backup_path)
    else:
        Path(backup_path).write_text("", encoding="utf-8")
    return backup_path


def restore_bib_backup(path: str, backup_path: str) -> None:
    """Restore a BibTeX file from a backup file."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    shutil.copyfile(backup_path, file_path)


def _entry_fields_for_preserve(entry: BibtexEntry, bib_path: str) -> dict[str, str]:
    normalized = _normalize_file_field(entry, bib_path)
    return {str(key).lower(): str(value) for key, value in normalized["fields"].items()}


def _render_write_plan_preserving_source(path: str, source: str, plan: WritePlan) -> str:
    entry = _normalize_file_field(plan["entry"], path)
    if plan["action"] == "insert":
        rendered = serialize_bibtex([entry])
        return append_entry_preserving_source(source, rendered)
    _validate_source_patchable(source)
    changed = plan.get("changed_fields") or []
    field_names = {
        _record_field_to_bibtex_field(str(name).lower())
        for name in changed
        if str(name).lower() != "citekey"
    }
    fields = _entry_fields_for_preserve(entry, path)
    patch_fields = {name: fields[name] for name in field_names if name in fields}
    if not patch_fields:
        return source
    return patch_entry_fields_preserving_source(source, entry["citekey"], patch_fields)


def _write_plan_preserving_source(path: str, source: str, plan: WritePlan) -> None:
    new_source = _render_write_plan_preserving_source(path, source, plan)
    if new_source == source:
        return
    create_bib_backup(path)
    _write_bib_text_atomic(path, new_source)


def _validate_source_patchable(source: str) -> None:
    errors = bibtex_source_errors(source)
    if not errors:
        return
    first = errors[0]
    raise ValueError(
        "malformed BibTeX: refusing to patch existing source; "
        f"{first.message} at line {first.line}, column {first.column}. "
        "Fix the .bib file manually or append a new entry instead."
    )


def _source_diff(old_source: str, new_source: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
        )
    )


def _record_field_to_bibtex_field(name: str) -> str:
    return {
        "authors": "author",
        "venue": "journal",
        "tags": "keywords",
        "local_pdf_path": "file",
    }.get(name, name)


def execute_write_plan(path: str, plan: WritePlan) -> list[BibtexEntry]:
    """Read, apply a plan, and write a BibTeX file under an exclusive lock.

    Validates that the resulting BibTeX round-trips through
    serialize → parse before committing to disk.
    """
    with with_bib_lock(path):
        source = _read_bib_source(path)
        if plan["action"] == "update":
            _validate_source_patchable(source)
        read_result = _read_bib_file_raw(path)
        current = read_result["entries"]
        if plan["action"] == "update":
            _validate_update_plan_against_current(read_result["records"], plan)
        if plan["action"] == "insert":
            plan = _rebase_insert_plan_against_current(read_result["records"], plan)
        updated = apply_write_plan(current, plan)
        _validate_bibtex_roundtrip(updated)
        _write_plan_preserving_source(path, source, plan)
        return updated


def preview_write_plan(path: str, plan: WritePlan) -> dict[str, Any]:
    """Preview a write plan without mutating the BibTeX file."""
    with with_bib_lock(path, shared=True):
        source = _read_bib_source(path)
        if plan["action"] == "update":
            _validate_source_patchable(source)
        read_result = _read_bib_file_raw(path)
        current = read_result["entries"]
        if plan["action"] == "update":
            _validate_update_plan_against_current(read_result["records"], plan)
        if plan["action"] == "insert":
            plan = _rebase_insert_plan_against_current(read_result["records"], plan)
        updated = apply_write_plan(current, plan)
        _validate_bibtex_roundtrip(updated)
        new_source = _render_write_plan_preserving_source(path, source, plan)
        return {
            "changed": source != new_source,
            "diff": _source_diff(source, new_source, path),
            "new_source": new_source,
            "updated_entries": updated,
        }


def _validate_update_plan_against_current(
    current_records: list[NormalizedRecord], plan: WritePlan
) -> None:
    index = plan.get("index")
    if not isinstance(index, int) or index < 0 or index >= len(current_records):
        raise ValueError("stale update plan: target index no longer exists")
    planned_record = plan.get("record")
    if not isinstance(planned_record, dict):
        raise ValueError("stale update plan: missing planned record")
    planned_citekey = planned_record.get("citekey")
    current_citekey = current_records[index].get("citekey")
    if planned_citekey and current_citekey != planned_citekey:
        raise ValueError("stale update plan: target citekey changed")


def _rebase_insert_plan_against_current(
    current_records: list[NormalizedRecord], plan: WritePlan
) -> WritePlan:
    planned_record = plan.get("record")
    if not isinstance(planned_record, dict):
        raise ValueError("stale insert plan: missing planned record")
    planned_citekey = planned_record.get("citekey")
    if not isinstance(planned_citekey, str) or not planned_citekey.strip():
        return plan

    match_index = find_exact_match(cast(NormalizedRecord, planned_record), current_records)
    if match_index is not None:
        existing_record = current_records[match_index]
        merge_decision = merge_entries(
            cast(MergeableEntry, dict(existing_record)),
            cast(MergeableEntry, dict(planned_record)),
        )
        merged_record = merge_decision["merged"]
        entry_type = plan.get("entry", {}).get("entry_type", "article")
        return {
            **plan,
            "action": "update",
            "index": match_index,
            "record": merged_record,
            "entry": record_to_bibtex_entry(merged_record, entry_type=entry_type),
            "changed_fields": merge_decision["changed_fields"],
        }

    existing_keys = {
        citekey
        for record in current_records
        for citekey in [record.get("citekey")]
        if isinstance(citekey, str) and citekey.strip()
    }
    resolved = resolve_citekey_collision(planned_citekey.strip(), existing_keys)
    if resolved == planned_citekey.strip():
        return plan

    updated_record = dict(planned_record)
    updated_record["citekey"] = resolved
    updated_entry = dict(plan["entry"])
    updated_entry["citekey"] = resolved
    updated_plan = dict(plan)
    updated_plan["record"] = updated_record
    updated_plan["entry"] = updated_entry
    return updated_plan


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
        source = _read_bib_source(path)
        _validate_source_patchable(source)
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
            changed_fields = [
                field
                for field, value in updated_entry["fields"].items()
                if current_entry["fields"].get(field) != value
            ]
            _write_plan_preserving_source(
                path,
                source,
                {
                    "action": "update",
                    "index": index,
                    "record": bibtex_entry_to_record(updated_entry),
                    "entry": updated_entry,
                    "changed_fields": changed_fields,
                },
            )
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


_ITEM_TYPE_TO_ENTRY_TYPE: dict[str, str] = {
    "journalArticle": "article",
    "conferencePaper": "inproceedings",
    "book": "book",
    "bookSection": "incollection",
    "thesis": "phdthesis",
    "preprint": "unpublished",
    "webpage": "unpublished",
    "report": "techreport",
    "manuscript": "unpublished",
    "presentation": "unpublished",
    "computerProgram": "misc",
}


def _resolve_entry_type(record: NormalizedRecord) -> str:
    """Determine BibTeX entry type from record metadata."""
    from pzi.promote_service import detect_preprint_source

    item_type = record.get("item_type")
    if isinstance(item_type, str) and item_type.strip():
        mapped = _ITEM_TYPE_TO_ENTRY_TYPE.get(item_type.strip())
        if mapped is not None:
            return mapped

    if detect_preprint_source(record) is not None:
        return "unpublished"

    return "article"


def plan_bib_write(
    incoming_record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    entry_type: str = "article",
) -> WritePlan:
    """Plan an insert or update operation for a normalized record."""
    if entry_type == "article":
        entry_type = _resolve_entry_type(incoming_record)

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
