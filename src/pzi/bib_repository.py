"""Deterministic BibTeX repository helpers: I/O, locking, write planning."""

from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypeAlias, TypedDict, cast

import portalocker
from bibtexparser.library import Library
from bibtexparser.model import Entry as BibtexEntryV2

from pzi.bib_serialize import (
    _bibtex_entry_to_library_entry,
    _library_to_entries_records,
    _normalize_file_field,
    _parse_bib_library,
    _resolve_file_field,
    _serialize_library,
    _validate_bibtex_roundtrip,
    _validate_library_parseable,
    parse_bibtex,
    serialize_bibtex,
)
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    bibtex_entry_to_record,
    record_to_bibtex_entry,
    resolve_citekey_collision,
)
from pzi.fileio import read_text_utf8
from pzi.similarity import (
    IdentityKind,
    build_identity_index,
    extract_identities,
    find_exact_match,
)


class ConcurrentEditError(RuntimeError):
    """Raised when the bib file is modified externally during a write operation."""


def _source_digest(text: str) -> str:
    """Content hash of bib source, used to detect external edits across the lock."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


class WritePlan(TypedDict):
    """An insert/update operation planned against a BibTeX library.

    The five core keys are always present; only ``force_new`` is optional (set
    by force-new inserts).  It is a plain ``dict`` at runtime, so dynamic
    construction / spreads still work — ``cast`` at sites that build it from a
    plain dict copy.
    """

    action: WriteAction
    index: int | None
    record: NormalizedRecord
    entry: BibtexEntry
    changed_fields: list[str]
    force_new: NotRequired[bool]


# Loose record-shaped dict accepted by merge_entries (carries arbitrary keys).
MergeableEntry: TypeAlias = dict[str, Any]


class MergeDecision(TypedDict):
    """Result of :func:`merge_entries`: the merged record and what changed."""

    merged: NormalizedRecord
    changed_fields: list[str]


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

    text = read_text_utf8(path)
    entries = parse_bibtex(text)
    records: list[NormalizedRecord] = [bibtex_entry_to_record(entry) for entry in entries]
    for record, entry in zip(records, entries):
        _resolve_file_field(record, entry, path)
    return {"entries": entries, "records": records}


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
        os.fsync(fd)  # flush to disk before rename so a crash can't leave an empty bib
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
        os.fsync(fd)  # flush to disk before rename so a crash can't leave an empty bib
    finally:
        os.close(fd)
    os.replace(tmp, file_path)


def _read_bib_source(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return read_text_utf8(path)



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
    # Snapshot content before locking; compare by hash (not mtime) under the
    # lock so a fast same-second external edit can't slip past a coarse-
    # resolution mtime.
    digest_before = _source_digest(_read_bib_source(path))
    with with_bib_lock(path):
        source = _read_bib_source(path)
        if _source_digest(source) != digest_before:
            raise ConcurrentEditError(
                f"bib file {path} was modified externally "
                f"while acquiring lock; aborting to prevent data loss"
            )
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


def _invariant(condition: bool, message: str) -> None:
    """Raise on a violated internal invariant.

    Used instead of ``assert`` for load-bearing batch-state guards: ``assert``
    is stripped under ``python -O``, which would silently disable the only thing
    standing between a desynced batch and a corrupt write.
    """
    if not condition:
        raise RuntimeError(f"internal invariant violated: {message}")


def _index_positions(
    index: dict[tuple[IdentityKind, str], list[int]],
) -> dict[tuple[IdentityKind, str], set[int]]:
    """Normalize an identity index to ``key -> set(positions)`` for comparison.

    Position *order* within a bucket is irrelevant to dedup (any matching
    position is a hit), so consistency is compared set-wise; empty buckets are
    dropped so a pruned-vs-absent key is not a spurious mismatch.
    """
    return {key: set(positions) for key, positions in index.items() if positions}


@dataclass
class BatchWriteSession:
    """In-memory view of a bib opened for a batch of edits under one lock.

    Owns the three structures that must move in lockstep across a batch: the
    parsed ``entries``, their projected ``records``, and the identity ``index``
    used for exact-match dedup.  Callers apply each planned edit through
    :meth:`apply_plan` rather than mutating the lists directly, so the
    entries/records parallelism (relied on by :func:`_update_library_blocks`)
    and the identity index stay coherent in one place.
    """

    entries: list[BibtexEntry]
    records: list[NormalizedRecord]
    index: dict[tuple[IdentityKind, str], list[int]]

    def apply_plan(self, plan: WritePlan) -> None:
        """Fold one write plan into the in-memory state, keeping entries,
        records, and the identity index in sync."""
        planned_record = cast(NormalizedRecord, plan["record"])
        if plan["action"] == "insert":
            position = len(self.records)
            self.entries.append(plan["entry"])
            self.records.append(planned_record)
        else:
            idx = plan["index"]
            if idx is None:
                raise RuntimeError(
                    "internal invariant violated: "
                    "update plan always carries a concrete index"
                )
            position = idx
            # Drop the outgoing record's identity keys before adding the new
            # ones: an update can change a record's doi/arxiv/url (e.g. metadata
            # enrichment mid-batch), and a stale key would otherwise cause the
            # next record sharing that identity to register a false exact-match.
            self._remove_from_index(self.records[idx], idx)
            self.entries[idx] = plan["entry"]
            self.records[idx] = planned_record
        for identity in extract_identities(planned_record):
            self.index.setdefault((identity["kind"], identity["value"]), []).append(position)
        _invariant(
            len(self.entries) == len(self.records),
            f"batch state desync: {len(self.entries)} entries != {len(self.records)} records",
        )

    def _remove_from_index(self, record: NormalizedRecord, position: int) -> None:
        """Remove *position* from every identity bucket *record* contributes,
        pruning buckets that become empty."""
        for identity in extract_identities(record):
            key = (identity["kind"], identity["value"])
            positions = self.index.get(key)
            if not positions:
                continue
            if position in positions:
                positions.remove(position)
            if not positions:
                del self.index[key]

    def check_consistency(self) -> None:
        """Verify the in-memory state is internally coherent.

        Cheap O(N) guard run before the atomic write: a desync here means a
        stale or missing identity key, which would let a later record falsely
        dedup against the wrong entry.  Because the session is transactional,
        raising aborts the whole batch with nothing written.
        """
        _invariant(
            len(self.entries) == len(self.records),
            f"batch state desync: {len(self.entries)} entries != {len(self.records)} records",
        )
        _invariant(
            _index_positions(self.index) == _index_positions(build_identity_index(self.records)),
            "identity index out of sync with records",
        )


@contextmanager
def batch_write_session(
    path: str, *, file_path_style: str = "absolute", write: bool = True,
) -> Iterator[BatchWriteSession]:
    """Open a bib once for many edits, writing a single atomic time on exit.

    Reads and parses the library exactly once; the caller folds each edit in
    through :meth:`BatchWriteSession.apply_plan`.  On clean exit, when *write*
    is set and the rendered source changed, writes it atomically while
    preserving comments/``@string``/``@preamble``.

    This collapses N locked read-modify-write cycles (one per record) into one,
    and makes the whole batch transactional: if the caller raises, nothing is
    written.  It is the bulk path behind ``import``.
    """
    with with_bib_lock(path):
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        _validate_library_parseable(library)
        entries, records = _library_to_entries_records(library, path)
        session = BatchWriteSession(
            entries=entries, records=records, index=build_identity_index(records),
        )
        yield session
        if not write:
            return
        session.check_consistency()
        _validate_bibtex_roundtrip(session.entries)
        new_library = _update_library_blocks(
            library, session.entries, path, file_path_style=file_path_style
        )
        new_source = _serialize_library(new_library)
        if new_source != source:
            _write_bib_text_atomic(path, new_source)


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
    return cast(WritePlan, updated_plan)


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


# ---------------------------------------------------------------------------
# Whole-entry mutations that preserve non-entry blocks (comments, @string,
# @preamble) and honor file_path_style.  Used by tag/delete/merge/reindex so
# every mutation rides the same comment-preserving path as add/update, rather
# than the lossy full re-serialization in write_bib_file.
# ---------------------------------------------------------------------------


def delete_bib_entry(path: str, citekey: str) -> UpdateBibEntryResult:
    """Delete the first entry matching *citekey*, preserving all other blocks.

    Drops only the matching ``@entry`` block; comments, ``@string`` macros,
    ``@preamble`` blocks, and every other entry (including their ``file``
    paths) are left exactly as written.
    """
    with with_bib_lock(path):
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        _validate_library_parseable(library)

        new_blocks: list = []
        removed = False
        for block in library.blocks:
            if not removed and isinstance(block, BibtexEntryV2) and block.key == citekey:
                removed = True
                continue
            new_blocks.append(block)

        if not removed:
            return {"found": False, "entries": [], "entry": None, "record": None}

        new_library = Library(blocks=new_blocks)
        new_source = _serialize_library(new_library)
        if new_source != source:
            _write_bib_text_atomic(path, new_source)
        entries, _records = _library_to_entries_records(new_library, path)
        return {"found": True, "entries": entries, "entry": None, "record": None}


def merge_bib_entries(
    path: str,
    *,
    citekey_a: str,
    citekey_b: str,
    file_path_style: str = "absolute",
) -> dict[str, Any]:
    """Merge entry A into B (keeping B's citekey) under one exclusive lock.

    Reads fresh records, runs the conservative :func:`merge_entries`, replaces
    B's block in place and drops A's block, preserving every other block.
    Returns ``{"found", "merged_record", "changed_fields"}``.
    """
    with with_bib_lock(path):
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        _validate_library_parseable(library)
        entries, records = _library_to_entries_records(library, path)

        idx_a = _find_entry_index(entries, citekey_a)  # type: ignore[arg-type]
        idx_b = _find_entry_index(entries, citekey_b)  # type: ignore[arg-type]
        if idx_a is None or idx_b is None:
            return {"found": False, "merged_record": None, "changed_fields": []}

        decision = merge_entries(
            cast(MergeableEntry, dict(records[idx_b])),
            cast(MergeableEntry, dict(records[idx_a])),
        )
        merged_record = decision["merged"]
        entry_type = entries[idx_b].get("entry_type", "article")
        merged_entry = record_to_bibtex_entry(
            cast(NormalizedRecord, merged_record), entry_type=entry_type
        )
        _validate_bibtex_roundtrip([merged_entry])
        merged_block = _bibtex_entry_to_library_entry(
            merged_entry, path, file_path_style=file_path_style
        )

        new_blocks: list = []
        removed_a = False
        replaced_b = False
        for block in library.blocks:
            if isinstance(block, BibtexEntryV2):
                if not removed_a and block.key == citekey_a:
                    removed_a = True
                    continue
                if not replaced_b and block.key == citekey_b:
                    replaced_b = True
                    new_blocks.append(merged_block)
                    continue
            new_blocks.append(block)

        new_library = Library(blocks=new_blocks)
        new_source = _serialize_library(new_library)
        if new_source != source:
            _write_bib_text_atomic(path, new_source)
        return {
            "found": True,
            "merged_record": merged_record,
            "changed_fields": decision["changed_fields"],
        }


def rewrite_entries_in_order(
    path: str,
    entries: list[BibtexEntry],
    *,
    file_path_style: str = "absolute",
) -> list[BibtexEntry]:
    """Rewrite all entries in their existing order, preserving non-entry blocks.

    Requires *entries* to be in the same order and count as the on-disk entry
    blocks (positional replace, which also supports citekey renames).  Used by
    reindex; comment positions, ``@string``, and ``@preamble`` are preserved.
    """
    with with_bib_lock(path):
        source = _read_bib_source(path)
        library = _parse_bib_library(source)
        _validate_library_parseable(library)
        existing_entries, _records = _library_to_entries_records(library, path)
        if len(entries) != len(existing_entries):
            raise ValueError(
                "rewrite_entries_in_order requires the same number of entries "
                f"as on disk (got {len(entries)}, expected {len(existing_entries)})"
            )
        _validate_bibtex_roundtrip(entries)
        new_library = _update_library_blocks(
            library, entries, path, file_path_style=file_path_style
        )
        new_source = _serialize_library(new_library)
        if new_source != source:
            _write_bib_text_atomic(path, new_source)
        return entries


# Public aliases for helpers consumed across modules — callers should not reach
# for the underscore-private names.
read_bib_file_raw = _read_bib_file_raw
parse_bib_library = _parse_bib_library
validate_library_parseable = _validate_library_parseable
find_entry_index = _find_entry_index
read_bib_source = _read_bib_source
validate_bibtex_roundtrip = _validate_bibtex_roundtrip


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
    index: dict[tuple[Any, str], list[int]] | None = None,
) -> WritePlan:
    """Plan an insert or update operation for a normalized record.

    *index* is an optional prebuilt identity index (see
    :func:`pzi.similarity.build_identity_index`) over *existing_records*, reused
    to avoid rebuilding it on each call in the write path.
    """
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

    match_index = find_exact_match(incoming_record, list(existing_records), index=index)
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
        "merged": cast(NormalizedRecord, merged),
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
