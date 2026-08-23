"""Deterministic BibTeX repository helpers: I/O, locking, applying writes.

Write *planning* — how a record and an entry merge into the thing a write will
say — lives in :mod:`pzi.bib_merge`, and is re-exported here so that every
caller still reaches it through this module.
"""

from __future__ import annotations

import contextlib
import difflib
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, TypeAlias, cast

import portalocker
from bibtexparser.library import Library
from bibtexparser.model import Entry as BibtexEntryV2

from pzi import exit_codes
from pzi.bib_merge import (
    MergeableEntry,
    WritePlan,
    _apply_untouched_fields_from_current,
    _carry_unmodelled_fields,
    merge_entries,
)

# Re-exported, not used here: the two planning entry points moved to
# `bib_merge` with the rest of the pure cluster, and every caller still reaches
# them through this module. `x as x` marks a deliberate re-export for ruff and
# pyright; isort keeps each aliased name on its own statement.
from pzi.bib_merge import plan_bib_write as plan_bib_write
from pzi.bib_merge import resolve_entry_type as resolve_entry_type
from pzi.bib_serialize import (
    bibtex_entry_to_library_entry,
    build_library,
    detect_bib_layout,
    library_to_entries_records,
    merge_preserving_unchanged_source,
    parse_bib_library,
    parse_bibtex_with_failures,
    resolve_file_field,
    resolved_bib_dir,
    serialize_library,
    validate_bibtex_roundtrip,
    validate_library_parseable,
)
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    apply_record_to_entry,
    bibtex_entry_to_record,
    merge_projected_entry,
    record_to_bibtex_entry,
    resolve_citekey_collision,
)
from pzi.errors import PziError
from pzi.fileio import fsync_parent_dir, read_text_utf8, write_all
from pzi.similarity import (
    IdentityKind,
    build_identity_index,
    extract_identities,
    find_exact_match,
)


class ConcurrentEditError(RuntimeError):
    """Raised when a concurrent edit makes a write unsafe to complete.

    Not a general "the file changed" signal — a change the write can absorb is
    rebased (see :func:`execute_write_plan`). This is for the case a batch
    session cannot absorb: :func:`pzi.promote_service._apply_published_fork`
    finds the citekey it is about to fork onto already present under the lock.
    """


def find_entry_index(entries: Sequence[dict[str, Any]], citekey: str) -> int | None:
    """Return index of first entry with the given citekey, or None."""
    return next(
        (i for i, entry in enumerate(entries) if entry["citekey"] == citekey),
        None,
    )


#: How long to wait for a bib lock before giving up. Generous on purpose: the
#: batch write path holds the lock across an entire import, PDF downloads
#: included, so anything short would fail legitimate concurrent use. Its job is
#: to turn "hangs forever behind a wedged holder" into the exit 5 that
#: `exit_codes.ENVIRONMENT` already promises for a locked bib.
LOCK_TIMEOUT_SECONDS = 300.0
#: How often the wait polls while blocked.
LOCK_POLL_SECONDS = 0.25


def acquire_lock_with_timeout(
    lock_fh: Any,
    flags: portalocker.LockFlags,
    *,
    bib_path: str,
    timeout: float,
) -> None:
    """Take the lock, giving up after *timeout* instead of blocking forever.

    `portalocker.lock` takes no timeout and blocks in the kernel, so a wedged
    holder hung pzi silently with no message and no exit code — the one case
    `exit_codes.ENVIRONMENT` names but nothing could produce, since every
    write-path refusal fires *after* the lock is acquired.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            portalocker.lock(lock_fh, flags | portalocker.LOCK_NB)
            return
        except portalocker.exceptions.BaseLockException:
            if time.monotonic() >= deadline:
                # No "remove the stale lock file" advice: `flock` is released by
                # the kernel when the holder exits, so a leftover lock file is
                # never stale — deleting it while a holder is live gives the next
                # `open()` a fresh inode and lets a second writer in immediately.
                raise PziError(
                    f"timed out after {timeout:.0f}s waiting for the lock on "
                    f"{bib_path} — another pzi process is still holding it",
                    code=exit_codes.ENVIRONMENT,
                ) from None
            time.sleep(LOCK_POLL_SECONDS)


@contextmanager
def with_bib_lock(
    bib_path: str, shared: bool = False, *, timeout: float = LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Take an advisory lock scoped to a bib file.

    Acquires an exclusive lock by default (for writes/updates).
    Pass shared=True for a shared lock (for reads).

    Uses portalocker, which provides cross-platform file locking
    (fcntl on Unix, LockFileEx on Windows).

    The lock is named after the *canonical* target (:func:`_resolve_write_target`),
    which is also the file the write replaces. Naming it after the configured
    path instead let a symlink and its real path — or two symlinks to one bib —
    take two different locks while replacing the same file, i.e. no mutual
    exclusion at all for the case that matters.
    """
    lock_path = Path(str(_resolve_write_target(bib_path)) + ".lock")
    flags = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
    if shared:
        # A shared lock is only meaningful against writers, and a writer needs a
        # writable directory anyway — so when the lock file cannot be created,
        # read without one rather than refusing to read at all. Creating it
        # unconditionally made a bib on a read-only mount, or in a directory
        # owned by someone else, unreadable, and blamed a `.lock` file the user
        # has never heard of.
        try:
            # No `mkdir` on the read path: a read has no business materializing
            # a directory tree for a bib that is not there, which is how a
            # typo'd path quietly became the start of a second library.
            lock_fh = open(str(lock_path), "a")
        except OSError:
            yield
            return
        with lock_fh:
            acquire_lock_with_timeout(lock_fh, flags, bib_path=bib_path, timeout=timeout)
            try:
                yield
            finally:
                portalocker.unlock(lock_fh)
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(lock_path), "a") as lock_fh:
        acquire_lock_with_timeout(lock_fh, flags, bib_path=bib_path, timeout=timeout)
        try:
            yield
        finally:
            portalocker.unlock(lock_fh)


ReadBibResult: TypeAlias = dict[str, Any]



UpdateBibEntryResult: TypeAlias = dict[str, Any]



def apply_write_plan(entries: list[BibtexEntry], plan: WritePlan) -> list[BibtexEntry]:
    """Apply an insert/update write plan to parsed BibTeX entries.

    ``plan["entry"]`` is authoritative and replaces the entry at
    ``plan["index"]``.  :meth:`BatchWriteSession.apply_plan` does the same, so a
    plan means one thing wherever it is applied — the merge onto the on-disk
    entry belongs at plan *construction* (see :func:`plan_bib_write`).
    """
    updated_entries = list(entries)
    if plan["action"] == "insert":
        updated_entries.append(plan["entry"])
        return updated_entries

    index = plan["index"]
    if index is None:
        # No caller in the tree reaches this — `plan_bib_write` always sets
        # `index` for an update — but ``WritePlan`` types it `int | None`, so a
        # hand-assembled plan can express it. The alternative to raising here is
        # `updated_entries[None]`, i.e. a `TypeError` out of a write path.
        raise PziError(
            "cannot apply the update: the write plan names no entry to replace",
            code=exit_codes.ENVIRONMENT,
        )
    updated_entries[index] = plan["entry"]
    return updated_entries


def describe_missing_bib(path: str) -> str | None:
    """A warning naming *path* when the configured bib is not there, else None.

    The `.bib` **is** the database, so "this file does not exist" and "this
    library is empty" are different facts, and reporting the first as the second
    is how a typo'd path, a renamed file or an unmounted share showed up as a
    healthy library with zero entries and exit 0.

    A *warning* and not a refusal, because the filesystem cannot tell a typo
    from a library nobody has captured into yet: a freshly `pzi init`-ed config
    points at a bib that does not exist until the first `add` creates it, and
    erroring there would break the normal first run. Naming the path lets the
    user tell the two apart themselves.
    """
    if Path(path).exists():
        return None
    return f"bib file does not exist yet: {path}"


def read_bib_notices(path: str) -> list[str]:
    """Every read notice for *path* that is not about an individual block.

    One home for the fact, because the previous arrangement — each caller
    appending :func:`describe_missing_bib` itself — reached `bib_service` and
    none of the other six read sites, so only ``pzi entries`` said the library
    file was missing while `library check`, `search`, `tag list` and the three
    other `library` subcommands reported a healthy empty library at exit 0.
    """
    return [notice for notice in (describe_missing_bib(path),) if notice]


def read_bib_file(path: str) -> ReadBibResult:
    """Read a BibTeX file and project its entries into normalized records."""
    with with_bib_lock(path, shared=True):
        return read_bib_file_raw(path)


def read_bib_file_with_notices(path: str) -> tuple[ReadBibResult, list[str]]:
    """:func:`read_bib_file_with_failures` plus :func:`read_bib_notices`.

    The read entry point for anything that shows a user a count of entries.
    """
    result, failures = read_bib_file_with_failures(path)
    return result, [*read_bib_notices(path), *failures]


def read_bib_file_with_failures(path: str) -> tuple[ReadBibResult, list[str]]:
    """:func:`read_bib_file`, also reporting the blocks the parser dropped.

    The locked twin of :func:`read_bib_file_raw_with_failures`. Prefer this
    wherever the count of entries is shown to a user: a duplicate citekey keeps
    only the first block, so a plain `read_bib_file` reports fewer entries than
    the file contains and says nothing about it.
    """
    with with_bib_lock(path, shared=True):
        return read_bib_file_raw_with_failures(path)


def read_bib_file_raw(path: str) -> ReadBibResult:
    """Read BibTeX file without acquiring a lock (caller must lock)."""
    result, _failures = read_bib_file_raw_with_failures(path)
    return result


def read_bib_file_raw_with_failures(path: str) -> tuple[ReadBibResult, list[str]]:
    """Read a BibTeX file, also reporting entries the parser had to drop.

    Callers that present the read as complete — an export billed as a backup, an
    import reporting how many records it took — must not treat a lenient parse
    as a total one. Caller must hold the lock.
    """
    file_path = Path(path)
    if not file_path.exists():
        return {"entries": [], "records": []}, []

    text = read_text_utf8(path)
    entries, failures = parse_bibtex_with_failures(text)
    records: list[NormalizedRecord] = [bibtex_entry_to_record(entry) for entry in entries]
    for record, entry in zip(records, entries):
        resolve_file_field(record, entry, path)
    return {"entries": entries, "records": records}, failures


def _resolve_write_target(path: str) -> Path:
    """Resolve a configured bib path to the file that should actually be replaced.

    ``os.replace`` treats a symlink destination as the directory entry to
    replace, not the file it points at — so writing straight to a symlinked
    ``.bib`` path would delete the symlink and leave a regular file in its
    place, silently detaching it from whatever the symlink used to point to
    (e.g. a synced cloud-storage location). Resolving through the symlink
    first makes the write land on the real target, which is what a symlinked
    config path is for.
    """
    return Path(os.path.realpath(path))


#: Bytes of an existing bib inspected to decide its line-ending style. The style
#: is uniform in practice; reading the whole file only to count newlines would
#: double the cost of every write on a large library.
_NEWLINE_SNIFF_BYTES = 65536
_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class _TextShape:
    """Byte-level conventions of an existing bib that a rewrite must preserve.

    pzi works in decoded text with LF endings and no BOM, because that is what
    the parser and every diff want. Neither is the user's choice to lose: a
    Windows-authored bib rewritten with LF is a 100%-changed file in git after a
    one-tag edit, and a relocated BOM stops the file starting with an entry.
    """

    newline: str = "\n"
    bom: bool = False


def _detect_text_shape(file_path: Path) -> _TextShape:
    try:
        with open(file_path, "rb") as handle:
            head = handle.read(_NEWLINE_SNIFF_BYTES)
    except OSError:
        return _TextShape()
    crlf = head.count(b"\r\n")
    # Dominant style wins, so one stray LF in a CRLF file does not flip the
    # whole rewrite; a file with no newline at all keeps the LF default.
    newline = "\r\n" if crlf and crlf * 2 >= head.count(b"\n") else "\n"
    return _TextShape(newline=newline, bom=head.startswith(_BOM))


def _existing_file_mode(file_path: Path) -> int | None:
    """Permission bits of the file being replaced, or None if it is new."""
    try:
        return stat.S_IMODE(os.stat(file_path).st_mode)
    except OSError:
        return None


#: The shared writer — see `fileio.write_all`.
_write_all = write_all


def _write_bib_text_atomic(path: str, text: str) -> None:
    """Replace the bib at *path* with *text*, all-or-nothing.

    Writes a sibling temporary file, fsyncs it, then ``os.replace``s it over the
    target, so a crash or a failed write leaves the original untouched. Mode
    bits are carried over from the file being replaced.

    One fidelity limit is inherent to replace-based atomicity and is not a bug
    to fix here: a bib with more than one hard link keeps only the replaced name
    pointing at the new content, so the other links still see the old file.
    """
    file_path = _resolve_write_target(path)
    file_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    shape = _detect_text_shape(file_path)
    previous_mode = _existing_file_mode(file_path)
    if shape.newline != "\n":
        text = text.replace("\n", shape.newline)
    content = (_BOM if shape.bom else b"") + text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(file_path.parent), prefix=".bib-", suffix=".tmp")
    try:
        try:
            _write_all(fd, content)
            os.fsync(fd)  # flush to disk before rename so a crash can't leave an empty bib
        finally:
            os.close(fd)
        if previous_mode is not None:
            # mkstemp creates 0600; without this every write silently tightened
            # a 0644 bib and broke anything else reading it.
            os.chmod(tmp, previous_mode)
        os.replace(tmp, file_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    fsync_parent_dir(file_path)


def _write_bib_with_backup(path: str, text: str, backup_path: Path | None) -> None:
    """Write *text*, leaving a ``.bak`` of the replaced file only if it lands.

    The copy is taken here — under the caller's lock, immediately before the
    write — so the backup is exactly the content being replaced. It is removed
    again when the write raises, because a ``.bak`` from a write that did not
    happen is a snapshot of a file nothing replaced: the user is invited to
    restore over content that never changed, and :func:`backup_path_for` hands
    the next, real run a ``.bak2``. ``reindex_service`` unlinks its own backup
    on failure for the same reason; this is the same rule for the three
    block-destroying writers that share this path.
    """
    if backup_path is not None:
        backup_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copy2(path, backup_path)
    try:
        _write_bib_text_atomic(path, text)
    except BaseException:
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
        raise


def read_bib_source(path: str) -> str:
    """The bib's text, or ``""`` when the file is not there (caller must lock)."""
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
    touched_indices: Collection[int] | None = None,
) -> tuple[Library, frozenset[int]]:
    """Replace entry blocks in a Library with updated entries, preserving
    comments, strings, and preambles.

    *touched_indices* lists the positions the caller actually modified; every
    other position keeps its **original block object** rather than a projection
    rebuilt from the internal entry dict. Rebuilding them was lossy in two ways:
    it dropped the parser's record of each field's original enclosing —
    severing every ``@string`` macro reference in the file (see
    ``serialize_library``) — and it reformatted the entire library on every
    one-entry write.

    ``None`` means *every* position is touched, which rebuilds the whole
    library. That is the right answer only for callers that genuinely rewrite
    all entries (``rewrite_entries_in_order_locked``); a caller that passes it
    out of convenience reintroduces both losses above. An empty collection is
    therefore meaningfully different from ``None``: it says "touch nothing",
    which is what a pure insert does.

    What an untouched position keeps is its **fields** — values, enclosings and
    order — not its bytes. ``serialize_library`` renders every block with the
    one indent and trailing-comma style ``detect_bib_layout`` found dominant, so
    an entry written in the file's *minority* style is reformatted by a write
    that never named it. Pinned by
    ``test_an_untouched_minority_style_entry_is_reformatted``.
    """
    # Resolved once for the whole library: it is the same answer for every
    # entry, and `Path.resolve()` is a syscall walk paid per entry per write.
    bib_dir = resolved_bib_dir(bib_path) if bib_path else None
    remaining_entries = list(entries)

    def rebuild(entry: BibtexEntry) -> BibtexEntryV2:
        return bibtex_entry_to_library_entry(
            entry, bib_path, file_path_style=file_path_style, bib_dir=bib_dir
        )
    # ``entries`` comes from ``apply_write_plan``, which only ever replaces an
    # entry in place or appends one at the end. It is therefore in the same
    # order as the on-disk entry blocks, with inserts trailing. We replace
    # positionally (not by citekey) precisely so that an update which *renames*
    # a citekey still maps to its original block instead of being lost.
    # `@string` definitions, for deciding whether a rebuilt field value is just
    # the resolved form of the macro reference already on disk.
    strings = {definition.key: definition.value for definition in library.strings}

    new_blocks: list = []
    #: Output positions holding a block this call did not touch, so
    #: `serialize_library` can write their original bytes back instead of
    #: re-rendering them. Only ever a position whose block object is the one the
    #: parser produced and nothing mutated — `raw` goes stale on mutation, so a
    #: rebuilt or merged position must never be listed here.
    verbatim: set[int] = set()
    position = 0
    for block in library.blocks:
        if isinstance(block, BibtexEntryV2):
            # Each existing entry block must have a corresponding updated entry;
            # a shortfall would mean this path is silently dropping an entry.
            if not remaining_entries:  # pragma: no cover — invariant guard
                raise ValueError(
                    "internal error: fewer updated entries than existing blocks "
                    "while rendering BibTeX write plan"
                )
            entry = remaining_entries.pop(0)
            keep_original = touched_indices is not None and position not in touched_indices
            if keep_original:
                # Not rebuilt at all: on a 22k-entry library a one-field write
                # used to project every entry back through
                # `bibtex_entry_to_library_entry` and then discard all but one.
                verbatim.add(len(new_blocks))
                new_blocks.append(block)
            else:
                new_blocks.append(
                    merge_preserving_unchanged_source(block, rebuild(entry), strings)
                )
            position += 1
        else:
            # Comments, `@string` definitions and preambles are passed through
            # untouched by every write, so they are verbatim whenever anything is.
            if touched_indices is not None:
                verbatim.add(len(new_blocks))
            new_blocks.append(block)
    # Append any remaining new entries (inserts beyond original count).
    new_blocks.extend(rebuild(entry) for entry in remaining_entries)
    return build_library(new_blocks), frozenset(verbatim)


def _touched_index(plan: WritePlan) -> int | None:
    """The existing block index a plan rewrites, or None for a pure insert.

    Read the action *after* any rebase: rebasing can turn an insert into an
    update against an existing block. An insert touches no existing block, so
    None — meaning "rebuild nothing" — is the honest answer for it, not "rebuild
    everything".
    """
    return plan["index"] if plan["action"] == "update" else None


def _render_updated_library(
    library: Library,
    updated_entries: list[BibtexEntry],
    path: str,
    *,
    updated_index: int | None,
    source: str,
    file_path_style: str = "absolute",
) -> str:
    """Serialize *library* with *updated_entries* applied to it.

    *source* is the text being rewritten, and is read only to sniff the file's
    layout conventions (:func:`detect_bib_layout`) so the rewrite reproduces
    them.

    Takes what the caller already computed rather than recomputing it. The
    previous `_render_write_plan` re-read the same in-memory source, re-parsed
    the whole library, re-projected every entry to a record and re-applied the
    plan — all of which its three callers had just done, under the same lock, on
    the same string. Every write parsed the file twice; a dry-run followed by a
    real write parsed it four times.

    Collapsing the passes also tightens `validate_bibtex_roundtrip`: it now
    guards the exact entry list that gets serialized, rather than a first-pass
    list that merely matched the written one by determinism.
    """
    updated_library, verbatim = _update_library_blocks(
        library,
        updated_entries,
        path,
        file_path_style=file_path_style,
        touched_indices=set() if updated_index is None else {updated_index},
    )
    return serialize_library(
        updated_library, layout=detect_bib_layout(source), verbatim_positions=verbatim
    )


@contextmanager
def _prepare_write(
    path: str,
    plan: WritePlan,
    *,
    shared: bool,
    file_path_style: str = "absolute",
) -> Iterator[tuple[str, str, list[BibtexEntry]]]:
    """Read, gate, rebase and render one plan, holding the lock over the yield.

    Yields ``(source, new_source, updated_entries)`` to a body that decides what
    to do with it: :func:`execute_write_plan` commits, :func:`preview_write_plan`
    diffs. Those two were byte-identical from the read to the render, which is
    how they came to be two of the paths that "validated four different amounts"
    (see :func:`_gate_batch`); one body means one answer.

    *shared* picks the lock mode — a preview takes a read lock, a write an
    exclusive one — and is the only thing the two callers still differ on
    besides their return shape.
    """
    with with_bib_lock(path, shared=shared):
        source = read_bib_source(path)
        library = parse_bib_library(source)
        # Inserts are gated too. An insert rewrites the whole file just as an
        # update does, so adding to a bib with an unparseable block used to
        # re-emit that block under bibtexparser's `% WARNING Parsing failed`
        # header — a fresh copy of the marker on every subsequent add.
        validate_library_parseable(library)
        entries, records = library_to_entries_records(library, path)

        if plan["action"] == "update":
            _validate_update_plan_against_current(records, plan)
            # Rebased for the preview too, or `--dry-run` would show a diff the
            # real write does not produce.
            plan = _rebase_update_plan_against_current(records, entries, plan)
        if plan["action"] == "insert":
            plan = _rebase_insert_plan_against_current(records, entries, plan)

        updated_entries = apply_write_plan(entries, plan)
        validate_bibtex_roundtrip(updated_entries)

        new_source = _render_updated_library(
            library,
            updated_entries,
            path,
            updated_index=_touched_index(plan),
            source=source,
            file_path_style=file_path_style,
        )
        yield source, new_source, updated_entries


def execute_write_plan(
    path: str,
    plan: WritePlan,
    *,
    file_path_style: str = "absolute",
) -> list[BibtexEntry]:
    """Read, apply a plan, and write a BibTeX file under an exclusive lock.

    Validates that the resulting BibTeX round-trips through
    serialize → parse before committing to disk.

    A plan built against an older read is *rebased* under the lock rather than
    rejected: the read in :func:`_prepare_write` is the authoritative one, and
    `_validate_update_plan_against_current` /
    `_rebase_{update,insert}_plan_against_current` re-project the plan onto it.
    A stale plan's carried fields losing to a concurrent writer is handled
    there, in :func:`_rebase_update_plan_against_current`.
    """
    with _prepare_write(
        path, plan, shared=False, file_path_style=file_path_style
    ) as (source, new_source, updated_entries):
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
    #: Positions this batch has written, for `_update_library_blocks` — every
    #: other block is kept verbatim so the batch does not reformat the library
    #: or sever `@string` references in entries it never looked at.
    touched: set[int] = dataclass_field(default_factory=set)

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
            # `plan["entry"]` is authoritative, exactly as in `apply_write_plan`:
            # update plans arrive already merged onto the entry on disk (see
            # `plan_bib_write`'s `existing_entries`). Merging here as well would
            # give one plan type two different meanings depending on which sink
            # consumed it.
            self.entries[idx] = plan["entry"]
            self.records[idx] = planned_record
        self.touched.add(position)
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


def _open_batch_session(path: str) -> tuple[str, Library, BatchWriteSession]:
    """Read and gate the library once, returning ``(source, library, session)``.

    The caller must already hold the bib lock; that lock's mode is the only
    thing :func:`batch_write_session` and :func:`preview_batch_write` still
    decide for themselves about the read.
    """
    source = read_bib_source(path)
    library = parse_bib_library(source)
    validate_library_parseable(library)
    entries, records = library_to_entries_records(library, path)
    session = BatchWriteSession(
        entries=entries, records=records, index=build_identity_index(records),
    )
    return source, library, session


def _gate_batch(session: BatchWriteSession) -> None:
    """The two whole-library gates every batch path runs, dry run included.

    A preview must not report success for a batch the real write would refuse.
    These validate the *whole library*, not just the incoming entries, and a dry
    run otherwise only checks each incoming entry on its own — so a pre-existing
    entry that blocks the write was invisible until the real run.

    This costs a dry run roughly what the real write costs (measured: ~1.4s to
    ~5s on a 22k-entry library). That is the right price for a preview whose
    entire job is to predict the write, and it is why `execute_write_plan`,
    `preview_write_plan`, `batch_write_session` and `preview_batch_write` no
    longer validate four different amounts — the gap that took so long to
    characterize. Neither gate mutates.
    """
    session.check_consistency()
    validate_bibtex_roundtrip(session.entries)


def _render_batch(
    library: Library,
    session: BatchWriteSession,
    path: str,
    source: str,
    *,
    file_path_style: str,
) -> str:
    """Render what a gated batch session would write."""
    new_library, verbatim = _update_library_blocks(
        library,
        session.entries,
        path,
        file_path_style=file_path_style,
        touched_indices=session.touched,
    )
    return serialize_library(
        new_library, layout=detect_bib_layout(source), verbatim_positions=verbatim
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
        source, library, session = _open_batch_session(path)
        yield session
        _gate_batch(session)
        if not write:
            # A dry run stops here rather than rendering: the gates are what it
            # came for, and the render is the expensive half on a 22k library.
            return
        new_source = _render_batch(
            library, session, path, source, file_path_style=file_path_style
        )
        if new_source != source:
            _write_bib_text_atomic(path, new_source)


def preview_write_plan(
    path: str,
    plan: WritePlan,
    *,
    file_path_style: str = "absolute",
) -> dict[str, Any]:
    """Preview a write plan without mutating the BibTeX file.

    *file_path_style* must match what the corresponding
    :func:`execute_write_plan` will use, or the diff shows `file` paths in a
    style the real write would not produce.
    """
    with _prepare_write(
        path, plan, shared=True, file_path_style=file_path_style
    ) as (source, new_source, updated_entries):
        return {
            "changed": source != new_source,
            "diff": _source_diff(source, new_source, path),
            "new_source": new_source,
            "updated_entries": updated_entries,
        }


def preview_batch_write(
    path: str,
    apply_plans: Callable[[BatchWriteSession], None],
    *,
    file_path_style: str = "absolute",
) -> dict[str, Any]:
    """Render the diff a *batch* of plans would produce, without writing.

    :func:`preview_write_plan` previews one plan, so a command that performs two
    writes in one session could only ever preview half of what it does — which
    is how ``update --promote``'s keep mode came to show the new published entry
    while omitting the cross-reference note it also writes onto the preprint.
    *apply_plans* receives the same session the real write uses, so preview and
    write build their plans in one place.
    """
    with with_bib_lock(path, shared=True):
        source, library, session = _open_batch_session(path)
        apply_plans(session)
        _gate_batch(session)
        new_source = _render_batch(
            library, session, path, source, file_path_style=file_path_style
        )
        return {
            "changed": new_source != source,
            "diff": _source_diff(source, new_source, path),
            "new_source": new_source,
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


class StalePlanError(PziError):
    """A plan whose target moved before the lock, beyond what a rebase can fix.

    Distinct from a bare :class:`PziError` so the one caller that can do
    something about it — `add_service._execute_plan_with_retry`, which re-reads
    and replans — catches this and nothing else. Everything a rebase *can*
    absorb (an appended entry, a citekey collision, an identity that now
    matches) never reaches here.
    """


def _stale_plan(reason: str) -> StalePlanError:
    """The bib moved under a plan built before the lock — a retry usually wins.

    Reachable whenever a second writer commits in that window, so it is an
    ordinary runtime outcome and has to read like one rather than as a traceback.
    """
    return StalePlanError(
        f"the bib changed while this write was being prepared — {reason}; "
        "retry the command",
        code=exit_codes.ENVIRONMENT,
    )


def _malformed_plan(reason: str) -> PziError:
    """A plan that is wrong in itself, not one the bib moved under.

    Deliberately *not* a :class:`StalePlanError`: `add_service` retries those by
    replanning, and replanning cannot fix a plan whose shape is invalid — it
    would burn a second lock cycle, delete the downloaded PDF and then tell the
    user to retry a failure that is deterministic. A caller bug reads as a
    caller bug.
    """
    return PziError(
        f"cannot write this plan — {reason}", code=exit_codes.ENVIRONMENT
    )


def _validate_update_plan_against_current(
    current_records: list[NormalizedRecord], plan: WritePlan
) -> None:
    index = plan.get("index")
    if not isinstance(index, int) or index < 0 or index >= len(current_records):
        raise _stale_plan("the entry it targets no longer exists")
    planned_record = plan.get("record")
    if not isinstance(planned_record, dict):
        raise _malformed_plan("it carries no record to write")
    planned_citekey = planned_record.get("citekey")
    # A falsy planned citekey used to skip the comparison below entirely, so the
    # rebase went on to write onto whatever occupied `index` — the one thing this
    # function exists to prevent. Malformed rather than stale, like the refusal
    # above it: the bib has not moved, the plan is wrong in itself, and
    # `add_service` replanning it would burn a lock cycle over a caller bug.
    # Unreachable from any producer today (`record_to_bibtex_entry` refuses a
    # keyless record at plan time), which is exactly why the guard should not
    # depend on that refusal for its own precondition.
    if not isinstance(planned_citekey, str) or not planned_citekey.strip():
        raise _malformed_plan("its record has no citekey to check against")
    current_citekey = current_records[index].get("citekey")
    if current_citekey != planned_citekey:
        raise _stale_plan("the entry it targets now has a different citekey")


#: `bibtex.USER_OWNED_FIELDS` in BibTeX spelling. These are the fields a rebase
#: restores from the on-disk entry, because their absence from a plan means "the
#: writer had no opinion", never "delete it" — unlike the identity fields
#: `promote --replace` strips deliberately. `citekey` is the entry key, not a
#: field, and is validated separately.
_USER_OWNED_ENTRY_FIELDS = ("note", "keywords", "file")


def _rebase_update_plan_against_current(
    current_records: list[NormalizedRecord],
    current_entries: list[BibtexEntry],
    plan: WritePlan,
) -> WritePlan:
    """Re-project the planned update onto the entry as it is *under the lock*.

    A plan is built long before it is executed: `add_service` reads the library,
    resolves metadata over the network and downloads a PDF, and only then calls
    `execute_write_plan`. Nothing before this point compares field content —
    `_validate_update_plan_against_current` checks the index and the citekey and
    nothing else — so without this rebase `plan["entry"]`, projected from a
    record read before any concurrent edit, was written verbatim over it,
    taking every unmodelled field (`volume`, `pages`, `publisher`, …) with it.

    Merging at the *entry* level is the starting point, because `plan["entry"]`
    is authoritative (`apply_write_plan` says so, and some callers build a plan
    whose entry carries a change its record does not). That alone is not enough,
    though: `plan["entry"]` is not a diff. `plan_bib_write` builds it by merging
    onto the entry it read at plan time, so it carries *stale copies* of fields
    this writer never touched, and letting those win silently reverted a
    concurrent writer — a `keywords` value added meanwhile went back to its
    plan-time value, and a record-owned field the projection omits (`journal`)
    was deleted outright by :func:`merge_projected_entry`. Both were reproduced
    against the released code, so this predates the removal of the pre-lock
    digest guard rather than following from it; the guard never covered it.

    So the plan is applied as a three-way merge against `plan["base_entry"]`
    (see :func:`_apply_untouched_fields_from_current`): planned-differs-from-base
    means this writer decided the field and it wins, planned-equals-base means it
    is a carried copy and the current entry wins. A plan built by hand carries no
    base — `promote --replace`'s preview is the one that matters — and keeps the
    older entry-level behaviour, which is what its deliberate identity-field
    stripping depends on.
    """
    index = plan.get("index")
    planned_record = plan.get("record")
    if not isinstance(index, int) or not isinstance(planned_record, dict):
        return plan
    if index < 0 or index >= len(current_entries) or index >= len(current_records):
        return plan  # already rejected by _validate_update_plan_against_current

    planned_entry = plan.get("entry")
    if not isinstance(planned_entry, dict):
        return plan
    current_entry = current_entries[index]

    # Merge at the *entry* level, because `plan["entry"]` is authoritative —
    # `apply_write_plan` says so, and some callers build a plan whose entry
    # carries a change its record does not. That keeps every unmodelled field
    # (`pages`, `publisher`, …) from the current entry and lets the plan win
    # everything it sets.
    rebased = merge_projected_entry(current_entry, planned_entry)
    # `merge_projected_entry` keeps the *existing* entry's type, which is right
    # when filling a gap and wrong here: `promote --replace` retypes
    # `@unpublished` to `@article` on purpose, and the plan is authoritative.
    rebased["entry_type"] = planned_entry["entry_type"]

    # …but "the plan is authoritative" only holds for what the plan actually
    # decided. Where a base is available, hand every other field back.
    base_entry = plan.get("base_entry")
    if isinstance(base_entry, dict):
        _apply_untouched_fields_from_current(
            rebased, base=base_entry, planned=planned_entry, current=current_entry
        )
        if planned_entry["entry_type"] == base_entry["entry_type"]:
            rebased["entry_type"] = current_entry["entry_type"]

    # `merge_projected_entry` treats a record-owned field the projection omits
    # as a deletion, which is correct for `promote --replace` — it strips
    # `arxiv_id`, the arXiv DOI and the preprint URLs on purpose — but wrong for
    # the user's own content, which the writer had no opinion about and simply
    # did not know existed yet. Restore exactly the user-owned fields, and only
    # where the plan does not set them.
    for field in _USER_OWNED_ENTRY_FIELDS:
        if field not in rebased["fields"] and field in current_entry["fields"]:
            rebased["fields"][field] = current_entry["fields"][field]

    # `changed_fields` is deliberately *not* recomputed here. It names the
    # fields this write decided, and this rebase does not change what was
    # decided — only which of those decisions the file ends up keeping. See the
    # field's own note on `WritePlan`.
    return cast(WritePlan, {**plan, "entry": rebased})


def _rebase_insert_plan_against_current(
    current_records: list[NormalizedRecord],
    current_entries: list[BibtexEntry],
    plan: WritePlan,
) -> WritePlan:
    planned_record = plan.get("record")
    if not isinstance(planned_record, dict):
        raise _malformed_plan("it carries no record to write")
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
        # Merge onto the entry on disk, the way `plan_bib_write` does. Applying a
        # bare projection of the merged record would drop every field the record
        # model does not carry (`volume`, `pages`, `publisher`, ...) and retype
        # the entry to whatever the stale plan assumed — turning the race this
        # rebase exists to absorb into silent data loss. Unlike there, no
        # snapshot-skew guard is needed: every caller derives both lists from one
        # `library_to_entries_records` call.
        _invariant(
            len(current_entries) == len(current_records),
            "rebase entry and record snapshots disagree",
        )
        return {
            **plan,
            "action": "update",
            "index": match_index,
            "record": merged_record,
            "entry": merge_projected_entry(
                current_entries[match_index],
                record_to_bibtex_entry(merged_record, entry_type=entry_type),
            ),
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
    backup_path: Path | None = None,
) -> UpdateBibEntryResult:
    """Update one BibTeX entry under lock using a citekey-scoped callback.

    *backup_path*, when given, is written from the on-disk file **inside this
    lock** and only when the write actually changes something, exactly as
    :func:`delete_bib_entry` does. Most updates add or fill a field and need no
    undo; `update --promote --replace` is the one that overwrites an entry's
    identity with a different paper's, which is a loss of the same kind.
    """
    with with_bib_lock(path):
        source = read_bib_source(path)
        library = parse_bib_library(source)
        validate_library_parseable(library)
        entries, records = library_to_entries_records(library, path)

        index = find_entry_index(entries, citekey)  # type: ignore[arg-type]
        if index is None:
            return {"found": False, "entries": entries, "entry": None, "record": None}

        current_entry = entries[index]
        current_record = records[index]
        previous_record = cast(NormalizedRecord, dict(current_record))
        updated_entry = updater(current_entry, current_record)
        updated_record = bibtex_entry_to_record(updated_entry)
        if updated_entry != current_entry:
            entries[index] = updated_entry
            # The round-trip gate the plan-based sinks have always had. This is
            # the path behind `tag`, `pdf attach/retry`, `update` and `promote`
            # — i.e. the commands that were writing unparseable libraries while
            # the safety net sat unwired two functions away.
            validate_bibtex_roundtrip(entries)
            # `entries` already has the update applied at `index`, which is
            # exactly what apply_write_plan would produce for this plan — so
            # there is nothing left to re-derive from `source`.
            new_source = _render_updated_library(
                library,
                entries,
                path,
                updated_index=index,
                source=source,
                file_path_style=file_path_style,
            )
            if new_source != source:
                _write_bib_with_backup(path, new_source, backup_path)
        return {
            "found": True,
            "entries": entries,
            "entry": updated_entry,
            "record": updated_record,
            # The record as it was before the callback ran. Callers that need to
            # report what changed diff this against `record` instead of having
            # the callback mutate a captured box to smuggle the answer out.
            "previous_record": previous_record,
        }


# ---------------------------------------------------------------------------
# Whole-entry mutations that preserve non-entry blocks (comments, @string,
# @preamble) and honor file_path_style.  Used by tag/delete/merge/reindex so
# every mutation rides the same comment-preserving path as add/update, rather
# than the lossy full re-serialization in write_bib_file.
# ---------------------------------------------------------------------------


def delete_bib_entry(
    path: str, citekey: str, *, backup_path: Path | None = None
) -> UpdateBibEntryResult:
    """Delete the first entry matching *citekey*, preserving all other blocks.

    Drops only the matching ``@entry`` block; comments, ``@string`` macros,
    ``@preamble`` blocks, and every other entry (including their ``file``
    paths) are left exactly as written.

    *backup_path*, when given, is written from the on-disk file **inside this
    lock**, immediately before the delete. The caller cannot do it itself: this
    function takes the only exclusive lock, and `with_bib_lock` opens a fresh
    descriptor each time, so an outer lock in the same process would block on
    itself. Copying outside the lock left the backup a snapshot of a version
    another writer may already have replaced.
    """
    with with_bib_lock(path):
        source = read_bib_source(path)
        library = parse_bib_library(source)
        validate_library_parseable(library)

        new_blocks: list = []
        removed = False
        for block in library.blocks:
            if not removed and isinstance(block, BibtexEntryV2) and block.key == citekey:
                removed = True
                continue
            new_blocks.append(block)

        if not removed:
            return {"found": False, "entries": [], "entry": None, "record": None}

        new_library = build_library(new_blocks)
        remaining, _remaining_records = library_to_entries_records(new_library, path)
        # The same round-trip gate the other write sinks apply. A delete cannot
        # invent a bad entry on its own, but it rewrites the whole file, so it
        # can commit one that was already unrepresentable — which is exactly how
        # a wedged library used to survive a `delete` unnoticed.
        validate_bibtex_roundtrip(remaining)
        new_source = serialize_library(new_library, layout=detect_bib_layout(source))
        if new_source != source:
            # Only when something is actually deleted, so a missing citekey
            # leaves no stray `.bak`; `_write_bib_with_backup` owns the rest.
            _write_bib_with_backup(path, new_source, backup_path)
        return {"found": True, "entries": remaining, "entry": None, "record": None}


def merge_bib_entries(
    path: str,
    *,
    citekey_a: str,
    citekey_b: str,
    file_path_style: str = "absolute",
    backup_path: Path | None = None,
) -> dict[str, Any]:
    """Merge entry A into B (keeping B's citekey) under one exclusive lock.

    Reads fresh records, runs the conservative :func:`merge_entries`, replaces
    B's block in place and drops A's block, preserving every other block.
    Returns ``{"found", "merged_record", "changed_fields", "dropped_fields"}``.

    Fields only A carries — ``volume``, ``pages``, ``publisher``, ``isbn`` and
    any custom key — are copied onto the survivor rather than deleted with A's
    block. B wins every conflict, matching :func:`merge_entries`' record
    semantics. ``dropped_fields`` names the ones that could not be carried
    (a conflict B already answers differently), so a caller can preview the
    loss instead of discovering it afterwards.

    *backup_path* is written from the on-disk file **inside this lock**,
    immediately before the write, exactly as :func:`delete_bib_entry` does — a
    merge destroys a block just as a delete does.

    Raises :exc:`PziError` when the two citekeys are the same. The block loop
    drops A's block and then replaces B's, and with one citekey there is only
    one block: it was removed as A and never restored as B, so the entry
    disappeared while the result reported ``found: True`` and no changed fields.
    `dedupe_service` guards its own call site, but this layer owns the data and
    holds the lock, so the precondition belongs here as well.
    """
    if citekey_a == citekey_b:
        raise PziError(
            f"cannot merge {citekey_a!r} with itself",
            code=exit_codes.ENVIRONMENT,
        )
    with with_bib_lock(path):
        source = read_bib_source(path)
        library = parse_bib_library(source)
        validate_library_parseable(library)
        entries, records = library_to_entries_records(library, path)

        idx_a = find_entry_index(entries, citekey_a)  # type: ignore[arg-type]
        idx_b = find_entry_index(entries, citekey_b)  # type: ignore[arg-type]
        if idx_a is None or idx_b is None:
            return {"found": False, "merged_record": None, "changed_fields": []}

        decision = merge_entries(
            cast(MergeableEntry, dict(records[idx_b])),
            cast(MergeableEntry, dict(records[idx_a])),
        )
        merged_record = decision["merged"]
        # Merge onto B's on-disk entry (B is the survivor), so fields the record
        # model does not carry survive the merge.
        merged_entry = apply_record_to_entry(
            entries[idx_b], cast(NormalizedRecord, merged_record)
        )
        merged_entry, dropped_fields = _carry_unmodelled_fields(
            merged_entry, entries[idx_a]
        )
        validate_bibtex_roundtrip([merged_entry])
        # Through `merge_preserving_unchanged_source`, as `_update_library_blocks`
        # does — a rebuilt block carries neither the parser's enclosing record nor
        # the user's field spelling, so writing it straight back rewrote
        # `journal = jmlr` as the literal token `{jmlr}` (severing the `@string`
        # reference while leaving the now-unreferenced macro behind) and
        # lowercased `Title`. The survivor is an entry this command was not asked
        # to reformat.
        strings = {definition.key: definition.value for definition in library.strings}
        merged_block = merge_preserving_unchanged_source(
            library.entries[idx_b],
            bibtex_entry_to_library_entry(
                merged_entry, path, file_path_style=file_path_style
            ),
            strings,
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

        new_library = build_library(new_blocks)
        new_source = serialize_library(new_library, layout=detect_bib_layout(source))
        if new_source != source:
            _write_bib_with_backup(path, new_source, backup_path)
        return {
            "found": True,
            "merged_record": merged_record,
            "changed_fields": decision["changed_fields"],
            "dropped_fields": dropped_fields,
        }


def backup_path_for(bib_path: str, citekey: str) -> Path:
    """A non-existing ``<bib>.<citekey>.bak`` path beside the bib.

    Shared by the two commands that destroy a block — ``delete`` and
    ``library merge`` — so both leave the same kind of trace under the same
    lock.
    """
    source = Path(bib_path)
    safe_citekey = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in citekey
    )
    base = source.with_name(f"{source.name}.{safe_citekey}.bak")
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = source.with_name(f"{source.name}.{safe_citekey}.bak{suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def rewrite_entries_in_order_locked(
    path: str,
    entries: list[BibtexEntry],
    *,
    file_path_style: str = "absolute",
) -> list[BibtexEntry]:
    """Rewrite all entries in their existing order, preserving non-entry blocks.

    **The caller must already hold the bib lock.**  Reindex renames PDFs on disk
    and rewrites the bib as one operation, so it takes the lock for the whole
    sequence; locking again here would nest.

    Requires *entries* to be in the same order and count as the on-disk entry
    blocks (positional replace, which also supports citekey renames).  Comment
    positions, ``@string``, and ``@preamble`` are preserved.
    """
    source = read_bib_source(path)
    library = parse_bib_library(source)
    validate_library_parseable(library)
    existing_entries, _records = library_to_entries_records(library, path)
    if len(entries) != len(existing_entries):
        # A user-facing message, not the internal function's name and arity:
        # this is reachable when the bib changes between planning a reindex and
        # writing it, which is an ordinary runtime outcome.
        raise PziError(
            f"the bib changed while the reindex was being prepared: {path} now "
            f"has {len(existing_entries)} entries, not {len(entries)} — "
            "retry the command",
            code=exit_codes.ENVIRONMENT,
        )
    validate_bibtex_roundtrip(entries)
    # No `touched_indices`: reindex rewrites the `file` field of every entry, so
    # every block really is touched and rebuilding all of them is correct here.
    new_library, verbatim = _update_library_blocks(
        library, entries, path, file_path_style=file_path_style
    )
    # `verbatim` is empty here by construction — no `touched_indices` means
    # every block is rebuilt — but it is threaded rather than dropped so this
    # site cannot drift from the other two.
    new_source = serialize_library(
        new_library, layout=detect_bib_layout(source), verbatim_positions=verbatim
    )
    if new_source != source:
        _write_bib_text_atomic(path, new_source)
    return entries
