"""Deterministic BibTeX repository helpers: I/O, locking, write planning."""

from __future__ import annotations

import difflib
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast

import portalocker
from bibtexparser.entrypoint import parse_string, write_string
from bibtexparser.library import Library
from bibtexparser.middlewares.enclosing import RemoveEnclosingMiddleware
from bibtexparser.model import Entry as BibtexEntryV2
from bibtexparser.model import Field
from bibtexparser.writer import BibtexFormat

from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    bibtex_entry_to_record,
    record_to_bibtex_entry,
    resolve_citekey_collision,
)
from pzi.similarity import find_exact_match


class ConcurrentEditError(RuntimeError):
    """Raised when the bib file is modified externally during a write operation."""


def _bib_mtime(path: str) -> float | None:
    """Return mtime of the bib file, or None if it does not exist."""
    p = Path(path)
    return p.stat().st_mtime if p.exists() else None


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

    Uses portalocker, which provides cross-platform file locking
    (fcntl on Unix, LockFileEx on Windows).
    """
    lock_path = Path(bib_path + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
    with open(str(lock_path), "a") as lock_fh:
        portalocker.lock(lock_fh, flags)
        try:
            yield
        finally:
            portalocker.unlock(lock_fh)


ReadBibResult: TypeAlias = dict[str, Any]



UpdateBibEntryResult: TypeAlias = dict[str, Any]



def parse_bibtex(text: str) -> list[BibtexEntry]:
    """Parse BibTeX text into entry dictionaries using bibtexparser v2."""
    library = parse_string(text)
    return [_library_entry_to_bibtex_entry(entry) for entry in library.entries]


def serialize_bibtex(entries: list[BibtexEntry]) -> str:
    """Serialize entries in a deterministic formatting style."""
    library = Library(blocks=[_bibtex_entry_to_library_entry(entry) for entry in entries])
    fmt = BibtexFormat()
    fmt.indent = "  "
    return write_string(library, bibtex_format=fmt)


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


def write_bib_file(
    path: str,
    entries: list[BibtexEntry],
    *,
    file_path_style: str = "absolute",
) -> None:
    """Write BibTeX entries to disk atomically.

    ``file_path_style`` controls how absolute ``file`` paths under the bib
    directory are serialized. Internal records still use absolute paths.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if file_path_style not in {"absolute", "relative"}:
        raise ValueError("file_path_style must be 'absolute' or 'relative'")
    if file_path_style == "relative":
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



def _parse_bib_library(raw_text: str) -> Library:
    """Parse BibTeX source text into a v2 Library."""
    if not raw_text:
        return Library(blocks=[])
    return parse_string(raw_text, parse_stack=[RemoveEnclosingMiddleware()])


def _validate_library_parseable(library: Library) -> None:
    """Raise ValueError if the library has unparseable blocks."""
    if not library.failed_blocks:
        return
    first = library.failed_blocks[0]
    raise ValueError(
        "malformed BibTeX: refusing to patch existing source; "
        f"parser error at around line {first.start_line}. "
        "Fix the .bib file manually or append a new entry instead."
    )


def _library_to_entries_records(
    library: Library, bib_path: str
) -> tuple[list[BibtexEntry], list[NormalizedRecord]]:
    """Extract entries and normalized records from a v2 Library."""
    entries = [_library_entry_to_bibtex_entry(e) for e in library.entries]
    records: list[NormalizedRecord] = [bibtex_entry_to_record(e) for e in entries]
    for record, entry in zip(records, entries):
        _resolve_file_field(record, entry, bib_path)
    return entries, records


def _serialize_library(library: Library) -> str:
    """Serialize a v2 Library to BibTeX text."""
    fmt = BibtexFormat()
    fmt.indent = "  "
    return write_string(library, bibtex_format=fmt)


def _update_library_blocks(
    library: Library,
    entries: list[BibtexEntry],
    bib_path: str,
    *,
    file_path_style: str = "absolute",
) -> Library:
    """Replace entry blocks in a Library with updated entries, preserving
    comments, strings, and preambles.
    """
    new_entry_blocks: list[BibtexEntryV2] = [
        _bibtex_entry_to_library_entry(e, bib_path, file_path_style=file_path_style)
        for e in entries
    ]
    # ``entries`` comes from ``apply_write_plan``, which only ever replaces an
    # entry in place or appends one at the end. It is therefore in the same
    # order as the on-disk entry blocks, with inserts trailing. We replace
    # positionally (not by citekey) precisely so that an update which *renames*
    # a citekey still maps to its original block instead of being lost.
    new_blocks: list = []
    for block in library.blocks:
        if isinstance(block, BibtexEntryV2):
            # Each existing entry block must have a corresponding updated entry;
            # a shortfall would mean this path is silently dropping an entry.
            if not new_entry_blocks:  # pragma: no cover — invariant guard
                raise ValueError(
                    "internal error: fewer updated entries than existing blocks "
                    "while rendering BibTeX write plan"
                )
            new_blocks.append(new_entry_blocks.pop(0))
        else:
            new_blocks.append(block)
    # Append any remaining new entries (inserts beyond original count).
    new_blocks.extend(new_entry_blocks)
    return Library(blocks=new_blocks)


def _render_write_plan(
    path: str,
    source: str,
    plan: WritePlan,
    *,
    file_path_style: str = "absolute",
) -> str:
    """Render the full BibTeX text after applying a write plan."""
    library = _parse_bib_library(source)
    if plan["action"] == "update":
        _validate_library_parseable(library)
    entries, records = _library_to_entries_records(library, path)

    if plan["action"] == "update":
        _validate_update_plan_against_current(records, plan)
    if plan["action"] == "insert":
        plan = _rebase_insert_plan_against_current(records, plan)

    updated_entries = apply_write_plan(entries, plan)
    updated_library = _update_library_blocks(
        library, updated_entries, path, file_path_style=file_path_style
    )
    return _serialize_library(updated_library)


def execute_write_plan(
    path: str,
    plan: WritePlan,
    *,
    file_path_style: str = "absolute",
) -> list[BibtexEntry]:
    """Read, apply a plan, and write a BibTeX file under an exclusive lock.

    Validates that the resulting BibTeX round-trips through
    serialize → parse before committing to disk.

    Raises :exc:`ConcurrentEditError` if the bib file is modified
    externally between snapshot and lock acquisition.
    """
    mtime_before = _bib_mtime(path)
    with with_bib_lock(path):
        if mtime_before is not None and _bib_mtime(path) != mtime_before:
            raise ConcurrentEditError(
                f"bib file {path} was modified externally "
                f"while acquiring lock; aborting to prevent data loss"
            )
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        if plan["action"] == "update":
            _validate_library_parseable(library)
        entries, records = _library_to_entries_records(library, path)

        if plan["action"] == "update":
            _validate_update_plan_against_current(records, plan)
        if plan["action"] == "insert":
            plan = _rebase_insert_plan_against_current(records, plan)

        updated_entries = apply_write_plan(entries, plan)
        _validate_bibtex_roundtrip(updated_entries)

        new_source = _render_write_plan(path, source, plan, file_path_style=file_path_style)
        if new_source != source:
            _write_bib_text_atomic(path, new_source)
        return updated_entries


def preview_write_plan(path: str, plan: WritePlan) -> dict[str, Any]:
    """Preview a write plan without mutating the BibTeX file."""
    with with_bib_lock(path, shared=True):
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        if plan["action"] == "update":
            _validate_library_parseable(library)
        entries, records = _library_to_entries_records(library, path)

        if plan["action"] == "update":
            _validate_update_plan_against_current(records, plan)
        if plan["action"] == "insert":
            plan = _rebase_insert_plan_against_current(records, plan)

        updated_entries = apply_write_plan(entries, plan)
        _validate_bibtex_roundtrip(updated_entries)

        new_source = _render_write_plan(path, source, plan)
        return {
            "changed": source != new_source,
            "diff": _source_diff(source, new_source, path),
            "new_source": new_source,
            "updated_entries": updated_entries,
        }


def _source_diff(old_source: str, new_source: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"{path} (before)",
            tofile=f"{path} (after)",
        )
    )


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

    match_index = None if plan.get("force_new") else find_exact_match(
        cast(NormalizedRecord, planned_record), current_records
    )
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
    *,
    file_path_style: str = "absolute",
) -> UpdateBibEntryResult:
    """Update one BibTeX entry under lock using a citekey-scoped callback."""
    with with_bib_lock(path):
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        _validate_library_parseable(library)
        entries, records = _library_to_entries_records(library, path)

        index = _find_entry_index(entries, citekey)  # type: ignore[arg-type]
        if index is None:
            return {"found": False, "entries": entries, "entry": None, "record": None}

        current_entry = entries[index]
        current_record = records[index]
        updated_entry = updater(current_entry, current_record)
        updated_record = bibtex_entry_to_record(updated_entry)
        if updated_entry != current_entry:
            entries[index] = updated_entry
            new_source = _render_write_plan(
                path,
                source,
                {
                    "action": "update",
                    "index": index,
                    "record": updated_record,
                    "entry": updated_entry,
                    "changed_fields": [
                        field
                        for field, value in updated_entry["fields"].items()
                        if current_entry["fields"].get(field) != value
                    ],
                },
                file_path_style=file_path_style,
            )
            if new_source != source:
                _write_bib_text_atomic(path, new_source)
        return {
            "found": True,
            "entries": entries,
            "entry": updated_entry,
            "record": updated_record,
        }


def _library_entry_to_bibtex_entry(entry: BibtexEntryV2) -> BibtexEntry:
    """Convert a bibtexparser v2 Entry to the internal BibtexEntry dict."""
    return {
        "entry_type": entry.entry_type,
        "citekey": entry.key,
        "fields": {f.key: f.value for f in entry.fields},
    }


def _bibtex_entry_to_library_entry(
    entry: BibtexEntry,
    bib_path: str = "",
    *,
    file_path_style: str = "absolute",
) -> BibtexEntryV2:
    """Convert an internal BibtexEntry dict to a bibtexparser v2 Entry.

    When requested, absolute ``file`` fields are normalised to relative
    paths. When *bib_path* is empty, no normalisation is performed (used
    for round-trip validation).
    """
    if bib_path and file_path_style == "relative":
        entry = _normalize_file_field(entry, bib_path)
    return BibtexEntryV2(
        entry_type=entry["entry_type"],
        key=entry["citekey"],
        fields=[
            Field(key=k, value=v)
            for k, v in sorted(entry["fields"].items())
        ],
    )


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
    force_new: bool = False,
) -> WritePlan:
    """Plan an insert or update operation for a normalized record."""
    if entry_type == "article":
        entry_type = _resolve_entry_type(incoming_record)

    if force_new:
        entry = record_to_bibtex_entry(incoming_record, entry_type=entry_type)
        return {
            "action": "insert",
            "index": None,
            "record": incoming_record,
            "entry": entry,
            "changed_fields": sorted(incoming_record.keys()),
            "force_new": True,
        }

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
