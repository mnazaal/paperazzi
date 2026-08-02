"""BibTeX serialization: text↔model↔dict conversion and injection-safety.

Pure except for ``file``-field handling, which resolves paths (and so follows
symlinks on disk) so stored references stay comparable.

This is the serialization layer underneath :mod:`pzi.bib_repository`: it parses
BibTeX source into the internal entry dicts, serializes them back
deterministically, resolves/normalizes the ``file`` field path, and neutralizes
untrusted metadata at the single serialization chokepoint so a hostile citekey
or field value cannot break out of its ``@type{...}`` / ``{...}`` delimiters.

It deliberately holds no locking, file I/O, write-planning, or merge logic —
those stay in :mod:`pzi.bib_repository`, which re-exports the names here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from bibtexparser.entrypoint import parse_string, write_string
from bibtexparser.library import Library
from bibtexparser.middlewares.enclosing import (
    AddEnclosingMiddleware,
    RemoveEnclosingMiddleware,
)
from bibtexparser.model import Block, DuplicateBlockKeyBlock, Field
from bibtexparser.model import Entry as BibtexEntryV2
from bibtexparser.writer import BibtexFormat

from pzi import exit_codes
from pzi.bibtex import BibtexEntry, NormalizedRecord, bibtex_entry_to_record
from pzi.errors import PziError


def parse_bibtex(text: str) -> list[BibtexEntry]:
    """Parse BibTeX text into entry dictionaries using bibtexparser v2."""
    library = parse_string(text)
    return [_library_entry_to_bibtex_entry(entry) for entry in library.entries]


def build_library(blocks: list[Block]) -> Library:
    """Build a ``Library`` from *blocks*, refusing duplicate entry keys.

    ``Library.add`` is "key-safe": a block whose key is already present is
    *silently* replaced with a ``DuplicateBlockKeyBlock``, which the writer then
    emits under a ``% WARNING Parsing failed`` header. Written to disk that
    wedges the library — the entry vanishes from ``entries``, ``export``
    refuses, and the next parse reports a failed block — so every site that
    constructs a library destined for disk goes through here instead.
    """
    library = Library(blocks=blocks)
    duplicates = [
        block.key
        for block in library.failed_blocks
        if isinstance(block, DuplicateBlockKeyBlock)
    ]
    if duplicates:
        listed = ", ".join(sorted(set(duplicates)))
        raise PziError(
            f"refusing to write: duplicate citekey {listed} — "
            "two entries would share one key, and BibTeX cannot represent that",
            code=exit_codes.ENVIRONMENT,
        )
    return library


def serialize_bibtex(entries: list[BibtexEntry]) -> str:
    """Serialize entries in a deterministic formatting style."""
    library = build_library(
        [_bibtex_entry_to_library_entry(entry) for entry in entries]
    )
    fmt = BibtexFormat()
    fmt.indent = "  "
    return write_string(library, bibtex_format=fmt)


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


def _parse_bib_library(raw_text: str) -> Library:
    """Parse BibTeX source text into a v2 Library."""
    if not raw_text:
        return Library(blocks=[])
    return parse_string(raw_text, parse_stack=[RemoveEnclosingMiddleware()])


def describe_failed_blocks(library: Library) -> list[str]:
    """One message per block bibtexparser could not turn into an entry.

    v2 *collects* these rather than raising, so any caller that reads only
    ``library.entries`` loses them without a signal. Duplicate citekeys land
    here too: the parser keeps the first block and files every later one as a
    failure, so it is equally an entry the caller never sees.
    """
    return [message for _key, message in failed_block_details(library)]


def failed_block_details(library: Library) -> list[tuple[str | None, str]]:
    """``(duplicate citekey or None, message)`` for each block the parser dropped.

    The key is what lets a caller tell the two kinds apart: a duplicate citekey
    is a readable file with a reported entry missing, while an unparseable block
    means the file cannot be trusted as a whole. `clean_service` needs that
    distinction to decide whether it is safe to go on computing counts.
    """
    details: list[tuple[str | None, str]] = []
    for block in library.failed_blocks:
        line = getattr(block, "start_line", None)
        where = f" at line {line + 1}" if isinstance(line, int) else ""
        key = getattr(block, "key", None)
        if isinstance(key, str) and key:
            details.append((
                key,
                f"duplicate citekey {key!r}{where}: only the first occurrence is read",
            ))
            continue
        detail = str(getattr(block, "error", "") or "").strip().splitlines()
        suffix = f": {detail[0]}" if detail else ""
        details.append((None, f"unparseable BibTeX block{where}{suffix}"))
    return details


def parse_bibtex_with_failures(text: str) -> tuple[list[BibtexEntry], list[str]]:
    """Parse BibTeX text, returning entries and a message per dropped block."""
    library = parse_string(text)
    entries = [_library_entry_to_bibtex_entry(entry) for entry in library.entries]
    return entries, describe_failed_blocks(library)


def _validate_library_parseable(library: Library) -> None:
    """Raise ValueError if the library has unparseable blocks.

    Guards the *write* paths: rewriting a file whose blocks the parser never
    saw would drop them. Read paths should report the same blocks via
    :func:`describe_failed_blocks` and carry on.
    """
    if not library.failed_blocks:
        return
    # `describe_failed_blocks` names the citekey for a duplicate and adds the
    # 1-based line; reuse it rather than re-deriving a worse message. The old
    # text interpolated the raw 0-based `start_line`, so a duplicate on line 4
    # was reported as "around line 3".
    detail = describe_failed_blocks(library)[0]
    raise ValueError(f"malformed BibTeX: refusing to rewrite the file — {detail}")


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
    """Serialize a v2 Library to BibTeX text, preserving original enclosings.

    ``_parse_bib_library`` strips enclosings with ``RemoveEnclosingMiddleware``,
    which records what it removed (including ``no-enclosing`` for a bare
    ``@string`` macro reference) on each block. The writer's *default* unparse
    stack discards that record and braces everything, which turns
    ``journal = jmlr`` into ``journal = {jmlr}`` — the ``@string`` block survives
    but every reference to it is severed. Reusing the recorded enclosing writes
    those fields back exactly as they were found.

    This only helps blocks that still carry the parser's record. Entries a write
    plan rebuilt (see ``_bibtex_entry_to_library_entry``) carry none, so they
    fall back to ``default_enclosing`` and are brace-wrapped. That is also what
    keeps ``_safe_field_value``'s injection guard meaningful: every value this
    code composes is enclosed, and only text read verbatim off disk is written
    back bare.

    A rebuilt entry keeps the source text of the fields a write did not change,
    via :func:`merge_preserving_unchanged_source`, so its own untouched macro
    references survive too.
    """
    fmt = BibtexFormat()
    fmt.indent = "  "
    return write_string(
        library,
        unparse_stack=[
            AddEnclosingMiddleware(
                default_enclosing="{",
                reuse_previous_enclosing=True,
                enclose_integers=True,
            )
        ],
        bibtex_format=fmt,
    )


def _validate_bibtex_roundtrip(entries: list[BibtexEntry]) -> None:
    """Raise if entries cannot survive a serialize→parse round-trip.

    A :exc:`PziError` from :func:`build_library` (a duplicate citekey) already
    says what is wrong in the user's own terms, so it passes through untouched;
    anything else is a library-internal failure and gets the generic wrapper.
    """
    try:
        text = serialize_bibtex(entries)
        parse_bibtex(text)
    except PziError:
        raise
    except Exception as exc:
        raise ValueError(
            f"write plan produces invalid BibTeX: {exc}"
        ) from exc


def merge_preserving_unchanged_source(
    original: BibtexEntryV2,
    rebuilt: BibtexEntryV2,
    strings: Mapping[str, str],
) -> BibtexEntryV2:
    """Take *rebuilt*, but keep *original*'s source text where nothing changed.

    A rebuilt block comes from the internal record model, which carries neither
    enclosings nor ``@string`` references: the record was read through
    ``parse_bibtex``, whose parse stack *resolves* macros. Writing it back
    verbatim rewrites ``journal = jmlr`` as the expanded literal, and turns an
    unresolved concatenation like ``acm # { Press}`` into a brace-quoted string
    that no longer concatenates — for fields the write never intended to touch.

    A field is treated as unchanged when *original*'s source text, resolved the
    way ``parse_bibtex`` would resolve it, equals *rebuilt*'s value. That test
    is self-verifying: if a plan changed a field, the rebuilt value necessarily
    differs from the resolved original and the rebuilt field wins. It
    deliberately does **not** consult ``changed_fields`` — trusting a plan's own
    account of what it changed would turn an under-reported field into silent
    data loss, where a wrong comparison here can only cost fidelity.

    Fields are emitted in *rebuilt*'s order, and *rebuilt* owns which fields
    exist at all, so a plan can still add and remove them.
    """
    original_fields = {field.key: field for field in original.fields}
    original_enclosing = original.parser_metadata.get("removed_enclosing", {})
    if not isinstance(original_enclosing, dict):  # pragma: no cover — defensive
        original_enclosing = {}

    fields: list[Field] = []
    preserved_enclosing: dict[str, str] = {}
    for field in rebuilt.fields:
        source_field = original_fields.get(field.key)
        if source_field is None:
            fields.append(field)
            continue
        enclosing = original_enclosing.get(field.key)
        if field.value not in _unchanged_forms(source_field.value, enclosing, strings):
            fields.append(field)
            continue
        fields.append(source_field)
        if isinstance(enclosing, str):
            preserved_enclosing[field.key] = enclosing

    merged = BibtexEntryV2(
        entry_type=rebuilt.entry_type, key=rebuilt.key, fields=fields,
    )
    # `parser_metadata` is bibtexparser's own (experimental) channel for this;
    # `_serialize_library`'s AddEnclosingMiddleware reads exactly this key to
    # decide which fields to write back unenclosed. Only preserved fields get an
    # entry, so every rebuilt value falls through to the default `{` enclosing.
    if preserved_enclosing:
        merged.parser_metadata["removed_enclosing"] = preserved_enclosing
    return merged


def _unchanged_forms(
    value: str, enclosing: object, strings: Mapping[str, str]
) -> frozenset[str]:
    """Every value a caller could hold that means "this field is as on disk".

    Two parse stacks feed write plans and they disagree about macros, so both
    readings have to count as unchanged:

    - ``parse_bibtex`` (``read_bib_file``, and so every service that plans from
      records) *resolves* a bare ``@string`` reference, yielding the definition.
    - ``_parse_bib_library`` (``update_bib_entry``, ``merge_bib_entries``) does
      not, yielding the macro name as written.

    The write path cannot adopt the resolving stack — resolution also erases the
    reference from blocks it never touches, which is the loss this whole
    mechanism exists to prevent — so the ambiguity is absorbed here instead.

    The one case this reads wrong: a plan that deliberately sets a field to the
    literal text of a macro name defined in the same file keeps the reference
    rather than becoming a string. Only a bare single-name reference resolves at
    all, matching bibtexparser, which leaves ``acm # { Press}`` as raw text.
    """
    if enclosing != "no-enclosing":
        return frozenset({value})
    return frozenset({value, strings.get(value.strip(), value)})


def _library_entry_to_bibtex_entry(entry: BibtexEntryV2) -> BibtexEntry:
    """Convert a bibtexparser v2 Entry to the internal BibtexEntry dict."""
    return {
        "entry_type": entry.entry_type,
        "citekey": entry.key,
        "fields": {f.key: f.value for f in entry.fields},
    }


# Citekeys are written as ``@type{<key>,`` (unquoted), and field values as
# ``{<value>}``.  Untrusted metadata (a hostile capture page, a crafted
# ``--citekey``/``--title``, a malicious ``--metadata-json``) could otherwise
# break out of those delimiters and inject or corrupt entries, so both are
# neutralized at this single serialization chokepoint.
#
# ``/`` is intentionally excluded: a citekey doubles as the PDF filename stem,
# so a path separator there has no legitimate use and would be one more way to
# smuggle path components toward the filesystem (paths are also basename-guarded
# downstream — this removes it at the source).
_UNSAFE_CITEKEY = re.compile(r"[^A-Za-z0-9_:.+\-]")
_UNSAFE_ENTRY_TYPE = re.compile(r"[^A-Za-z]")
# Control characters (keep \t and \n) — NUL and friends have no place in a
# BibTeX field value and can corrupt the file or downstream tools.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _safe_citekey(citekey: str) -> str:
    """Strip characters that could escape the ``@type{<key>,`` context."""
    cleaned = _UNSAFE_CITEKEY.sub("", citekey).strip(".")
    return cleaned or "untitled"


def _safe_field_value(value: str) -> str:
    """Make an untrusted field value safe to serialize inside ``{...}``."""
    return _balance_braces(_CONTROL_CHARS.sub("", value))


def _balance_braces(value: str) -> str:
    """Drop unmatched braces so a field value cannot terminate its ``{...}``.

    Balanced groups (e.g. case protection like ``{DNA}``) are preserved; only
    stray ``}`` (which would end the field early) and stray ``{`` are removed.
    """
    if "{" not in value and "}" not in value:
        return value
    kept: list[str] = []
    depth = 0
    for ch in value:  # left-to-right: drop unmatched closing braces
        if ch == "}":
            if depth == 0:
                continue
            depth -= 1
        elif ch == "{":
            depth += 1
        kept.append(ch)
    out: list[str] = []
    depth = 0
    for ch in reversed(kept):  # right-to-left: drop unmatched opening braces
        if ch == "{":
            if depth == 0:
                continue
            depth -= 1
        elif ch == "}":
            depth += 1
        out.append(ch)
    return "".join(reversed(out))


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
    entry_type = _UNSAFE_ENTRY_TYPE.sub("", entry["entry_type"]) or "misc"
    return BibtexEntryV2(
        entry_type=entry_type,
        key=_safe_citekey(entry["citekey"]),
        fields=[
            Field(key=k, value=_safe_field_value(v))
            for k, v in sorted(entry["fields"].items())
        ],
    )
