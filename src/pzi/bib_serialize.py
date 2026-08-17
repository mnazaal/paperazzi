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
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from bibtexparser.entrypoint import parse_string, write_string
from bibtexparser.library import Library
from bibtexparser.middlewares.enclosing import (
    AddEnclosingMiddleware,
    RemoveEnclosingMiddleware,
)
from bibtexparser.model import (
    Block,
    DuplicateBlockKeyBlock,
    ExplicitComment,
    Field,
    ImplicitComment,
)
from bibtexparser.model import Entry as BibtexEntryV2
from bibtexparser.writer import BibtexFormat

from pzi import exit_codes
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    bibtex_entry_to_record,
    parse_file_field,
    primary_pdf_path,
)
from pzi.errors import PziError


def parse_bibtex(text: str) -> list[BibtexEntry]:
    """Parse BibTeX text into entry dictionaries using bibtexparser v2."""
    library = parse_string(text)
    return [library_entry_to_bibtex_entry(entry) for entry in library.entries]


def build_library(blocks: Sequence[Block]) -> Library:
    """Build a ``Library`` from *blocks*, refusing duplicate entry keys.

    ``Library.add`` is "key-safe": a block whose key is already present is
    *silently* replaced with a ``DuplicateBlockKeyBlock``, which the writer then
    emits under a ``% WARNING Parsing failed`` header. Written to disk that
    wedges the library — the entry vanishes from ``entries``, ``export``
    refuses, and the next parse reports a failed block — so every site that
    constructs a library destined for disk goes through here instead.
    """
    library = Library(blocks=list(blocks))
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
    return _write_library_text(
        build_library([bibtex_entry_to_library_entry(entry) for entry in entries])
    )


def _write_library_text(library: Library) -> str:
    """Write *library* in a deterministic style, for validation round-trips.

    Deliberately **not** layout-aware: its two callers are
    :func:`serialize_bibtex` and :func:`validate_bibtex_roundtrip`, and neither
    output is ever written to a user's file — the second is reparsed and thrown
    away. Real writes go through :func:`serialize_library`, which takes a
    :class:`BibLayout` sniffed from the file being rewritten. Do not "fix" this
    one to match; a validation gate wants one canonical form.
    """
    fmt = BibtexFormat()
    fmt.indent = "  "
    return write_string(library, bibtex_format=fmt)


@dataclass(frozen=True)
class BibLayout:
    """How a ``.bib`` file lays its entries out, so a rewrite can reproduce it.

    bibtexparser's writer defaults are one house style among several, and pzi
    used to impose them on every write: two-space indent, no trailing comma, one
    blank line between entries. Rewriting one entry therefore rewrote the whole
    file — a Zotero or Better BibTeX export (tab indent, trailing commas) came
    back re-indented with its commas stripped, and a compact library gained a
    blank line per entry. On a 22k-entry library that is a 59.5k-line diff from
    adding one tag, which is indistinguishable from corruption at review time.

    ``block_separator`` is appended *after* a block that already ends in a
    newline (see bibtexparser's ``writer.write``), so ``"\\n"`` means one blank
    line between entries and ``""`` means none. bibtexparser's own default,
    ``"\\n\\n"``, therefore means *two* blank lines — which is nobody's
    convention, and is why every pzi write used to add a line per entry.
    """

    #: One blank line between entries: the conventional BibTeX look, and what a
    #: file with nothing to sniff (new, or a single entry) is written as.
    block_separator: str = "\n"
    #: The gap before a *comment* block, which is not always the gap before an
    #: entry. A Better BibTeX export writes its `% ==` quality report flush
    #: against the entry it describes while still separating entries by a blank
    #: line — so one separator for both boundaries cannot reproduce the file.
    #: Sniffed independently, and falls back to ``block_separator`` when the
    #: source has no comment boundary to learn from.
    comment_separator: str = "\n"
    indent: str = "  "
    trailing_comma: bool = False


#: A field line: leading whitespace, a key, then ``=``. Anchored per line so a
#: braced value spanning lines cannot be mistaken for one.
_FIELD_LINE_RE = re.compile(r"^([ \t]+)[A-Za-z][^\s=]*\s*=", re.MULTILINE)

#: The end of an entry: the last field's line ending, then the closing brace.
_ENTRY_END_RE = re.compile(r"(,?)[ \t]*\n[ \t]*\}[ \t]*(?:\n|$)")

#: The gap *between* two blocks: a closing brace on its own line, any blank
#: lines, then the next block. Only matches between blocks, so the file's final
#: entry cannot be mistaken for evidence about separation.
_BLOCK_GAP_RE = re.compile(r"^\}[ \t]*\n(\n*)(?=[ \t]*@)", re.MULTILINE)

#: The same gap, but before a *comment* rather than an entry. This boundary used
#: to be invisible to the sniffer — the lookahead above admits only `@` — while
#: the writer applied one separator at every boundary regardless. On a Better
#: BibTeX export, where almost every entry is followed by a flush `% ==` quality
#: report, that inserted a blank line before each one: measured at **18,650
#: blank lines from a single `tag add`** on a real 22,232-entry library, against
#: 3,582 entry→entry gaps that were the only ones being sampled.
_COMMENT_GAP_RE = re.compile(r"^\}[ \t]*\n(\n*)(?=[ \t]*%)", re.MULTILINE)


def _dominant_gap(source: str, pattern: re.Pattern[str], default: str) -> str:
    """The blank-line convention *pattern* finds, or *default* with no evidence.

    Dominant style wins, as everywhere else in this sniffer: one hand-edited
    boundary in a 22k-entry file must not flip the whole rewrite.
    """
    gaps = [match.group(1) for match in pattern.finditer(source)]
    if not gaps:
        return default
    blank_separated = sum(1 for gap in gaps if gap)
    return "\n" if blank_separated * 2 >= len(gaps) else ""


def detect_bib_layout(source: str) -> BibLayout:
    """Sniff *source*'s layout conventions, falling back to the writer defaults.

    Dominant style wins, the same rule ``_detect_text_shape`` uses for newlines:
    one hand-edited entry in a 22k-entry file must not flip the whole rewrite.
    A file with nothing to sniff — empty, or a single entry with no fields —
    keeps the defaults.

    Pure, and cheap enough to run on every write: three regex passes over text
    already in memory.
    """
    if not source.strip():
        return BibLayout()

    indents = [match.group(1) for match in _FIELD_LINE_RE.finditer(source)]
    indent = Counter(indents).most_common(1)[0][0] if indents else BibLayout.indent

    ends = [match.group(1) for match in _ENTRY_END_RE.finditer(source)]
    with_comma = sum(1 for end in ends if end == ",")
    trailing_comma = bool(ends) and with_comma * 2 >= len(ends)

    # A file with fewer than two blocks says nothing about how blocks are
    # separated, so it keeps the default rather than inventing evidence: one
    # entry ends in `}` too, and counting that would read "compact" off every
    # single-entry library.
    block_separator = _dominant_gap(source, _BLOCK_GAP_RE, BibLayout.block_separator)
    # Comment boundaries are sniffed separately, and a file with none of them
    # follows whatever the entry boundaries said — so a library without comments
    # is written exactly as it was before this distinction existed.
    comment_separator = _dominant_gap(source, _COMMENT_GAP_RE, block_separator)

    return BibLayout(
        block_separator=block_separator,
        comment_separator=comment_separator,
        indent=indent,
        trailing_comma=trailing_comma,
    )


def resolve_file_field(record: NormalizedRecord, entry: BibtexEntry, bib_path: str) -> None:
    """Resolve a relative ``file`` field to an absolute ``local_pdf_path``.

    When a BibTeX entry stores ``file = {papers/citekey.pdf}`` (relative
    to the bib file location), this helper resolves it to an absolute path
    so that internal consumers (PDF open, status checks) can locate the
    file without knowing the bib directory.

    Absolute paths and home-relative paths (``~/...``) are kept as-is.
    """
    # The path component, never the raw field: a Zotero/JabRef composite is
    # `description:path:mimetype`, and joining the bib dir to *that* produced
    # `<bib-dir>/Full Text PDF:/abs/x.pdf:application/pdf` — garbage neither
    # tool can read, written by any command that touched the entry.
    value = primary_pdf_path(entry.get("fields", {}).get("file"))
    if not value:
        return
    # Assign, do not `setdefault`: `bibtex_entry_to_record` always sets this key
    # (to `None` when there is no attachment), so `setdefault` was a no-op and
    # the absolute branch below never ran — which is exactly the Zotero case.
    if value.startswith(("/", "~")):
        record["local_pdf_path"] = value
        return
    # Best-effort relative resolution: <bib-dir>/<path>.
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
    # A composite is left alone entirely. Rewriting only its path component
    # would mean re-composing the field, which requires owning three producers'
    # escaping rules to gain nothing — and `merge_preserving_unchanged_source`
    # keeps the original text anyway whenever the attachment has not changed.
    if len(parse_file_field(value)) != 1 or parse_file_field(value)[0] != value:
        return entry
    if not value.startswith("/"):
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


def parse_bib_library(raw_text: str) -> Library:
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

    Entries the parser accepted but *mangled* are reported alongside them (see
    :func:`describe_mangled_field_keys`): the block is not "failed" by
    bibtexparser's reckoning, but a field of it is just as invisible.
    """
    return (
        [message for _key, message in failed_block_details(library)]
        + describe_mangled_field_keys(library)
        + describe_empty_citekeys(library)
        + describe_case_colliding_field_keys(library)
    )


def describe_case_colliding_field_keys(library: Library) -> list[str]:
    """One message per entry carrying two spellings of one field key.

    Field keys are case-folded at the parse boundary
    (:func:`library_entry_to_bibtex_entry`), which is what makes a JabRef-style
    ``Author =`` readable at all. The fold targets a ``dict``, so an entry
    holding *both* ``Title`` and ``title`` keeps only the last and the other
    value is gone — and nothing else catches it: bibtexparser flags only
    byte-identical duplicate keys, and :func:`validate_bibtex_roundtrip`
    compares the already-collapsed entry against itself. The first write to
    touch the entry commits the deletion.

    Such a file is invalid BibTeX (field names are case-insensitive), so this is
    a refusal rather than a repair: pzi cannot know which spelling the user
    meant to keep.
    """
    messages: list[str] = []
    for entry in library.entries:
        seen: dict[str, str] = {}
        collisions: dict[str, list[str]] = {}
        for field in entry.fields:
            folded = field.key.lower()
            if folded in seen:
                collisions.setdefault(folded, [seen[folded]]).append(field.key)
            else:
                seen[folded] = field.key
        for folded, spellings in collisions.items():
            line = getattr(entry, "start_line", None)
            where = f" at line {line + 1}" if isinstance(line, int) else ""
            names = ", ".join(f"`{name}`" for name in spellings)
            messages.append(
                f"entry '{entry.key}'{where} sets the field '{folded}' twice "
                f"({names}): BibTeX field names are case-insensitive, so keeping "
                "either one would silently drop the other — remove the spelling "
                "you do not want"
            )
    return messages


def describe_empty_citekeys(library: Library) -> list[str]:
    """One message per ``@type{,`` entry — parsed fine, but unusable and unwritable.

    bibtexparser accepts an entry with no key, so it is neither a failed block
    nor a mangled field: `pzi entries` listed it with a blank citekey column and
    exit 0, and nothing warned. The cost lands later and somewhere else — every
    *write* to the library, including one touching a different entry, is refused
    by `_safe_citekey` with "refusing to write an entry with an empty citekey",
    naming no file, no line and no entry.
    """
    messages: list[str] = []
    for entry in library.entries:
        if (entry.key or "").strip():
            continue
        line = getattr(entry, "start_line", None)
        where = f" at line {line + 1}" if isinstance(line, int) else ""
        messages.append(
            f"entry with no citekey{where} (`@{entry.entry_type}{{,`): give it a "
            "key — it cannot be cited, and it blocks every write to this library"
        )
    return messages


#: A field key can only pick these up by absorbing text that was meant to be
#: something else — a ``%`` comment line inside the entry, which bibtexparser
#: folds into the *following* field's key (``'% private note\n  doi'``), taking
#: that field's value out of the record model with it.
_MANGLED_FIELD_KEY = re.compile(r"[%\r\n]")


def describe_mangled_field_keys(library: Library) -> list[str]:
    """One message per field key that swallowed neighbouring text."""
    messages: list[str] = []
    for entry in library.entries:
        for field in entry.fields:
            if not _MANGLED_FIELD_KEY.search(field.key):
                continue
            real_key = field.key.rsplit("\n", 1)[-1].strip()
            messages.append(
                f"entry {entry.key!r}: a '%' comment inside the entry was folded "
                f"into the following field key, hiding {real_key!r} — "
                "move the comment outside the entry"
            )
    return messages


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
        suffix = _user_facing_parse_error(getattr(block, "error", ""))
        details.append((None, f"unparseable BibTeX block{where}{suffix}"))
    return details


#: bibtexparser appends implementation advice to some errors, e.g. "Duplicate
#: field keys on entry: 'title'.Note: The entry (containing duplicate) is
#: available as `failed_block.entry`". These messages are shown to the user as
#: the reason their file was refused, so the half that tells them to inspect a
#: Python attribute has to go.
_PARSER_INTERNAL_HINT = re.compile(r"\.?\s*Note:\s*The entry.*", re.IGNORECASE | re.DOTALL)


def _user_facing_parse_error(error: object) -> str:
    """Render a bibtexparser block error as a `: <reason>` suffix, or ``""``."""
    text = _PARSER_INTERNAL_HINT.sub("", str(error or "")).strip()
    first_line = text.splitlines()[0].strip() if text else ""
    return f": {first_line}" if first_line else ""


def parse_bibtex_with_failures(text: str) -> tuple[list[BibtexEntry], list[str]]:
    """Parse BibTeX text, returning entries and a message per dropped block."""
    library = parse_string(text)
    entries = [library_entry_to_bibtex_entry(entry) for entry in library.entries]
    return entries, describe_failed_blocks(library)


def parse_bibtex_for_import(text: str) -> tuple[list[BibtexEntry], list[str]]:
    """Parse *foreign* BibTeX: only entries that can be written back, and why not.

    :func:`parse_bibtex_with_failures` is deliberately lenient — an export billed
    as a backup should carry an entry whose field name the parser mangled, and
    merely warn. An *import* writes those entries into the user's library, where
    the same mangled name hides a field from every reader and refuses every later
    write, so here they are dropped rather than warned about.

    Each unusable entry produces exactly one message. Reporting it as both a
    dropped block and a failed record made a two-entry file import as "0/3".
    """
    library = parse_string(text)
    problems = [message for _key, message in failed_block_details(library)]
    entries: list[BibtexEntry] = []
    for block in library.entries:
        entry = library_entry_to_bibtex_entry(block)
        refusal = unwritable_field_key(entry)
        if refusal is None:
            entries.append(entry)
        else:
            problems.append(refusal)
    return entries, problems


def unwritable_field_key(entry: BibtexEntry) -> str | None:
    """Why :func:`serialize_bibtex` would refuse *entry*'s field names, or ``None``.

    Lets a caller holding entries read from a foreign file skip and report them
    one at a time, instead of losing a whole batch write to the first bad name.
    """
    for key in entry["fields"]:
        try:
            _checked_field_key(key, entry["citekey"])
        except PziError as exc:
            return exc.message
    return None


def validate_library_parseable(library: Library) -> None:
    """Raise :exc:`PziError` if the library has unparseable blocks.

    Guards the *write* paths: rewriting a file whose blocks the parser never
    saw would drop them. Read paths should report the same blocks via
    :func:`describe_failed_blocks` and carry on.
    """
    # A mangled field key is not a *failed* block — bibtexparser accepted it —
    # but rewriting the file would commit the mangling: the hidden field's value
    # is attached to a key no reader will ever match, and the whole thing
    # round-trips, so the write gate downstream sees nothing wrong.
    mangled = describe_mangled_field_keys(library)
    if mangled:
        raise _malformed_bib_refusal(mangled[0])
    # Refuse here rather than deep in `_safe_citekey`, which fires while
    # serializing and so names neither the file, the line, nor which entry —
    # and fires on writes that touch a completely different entry.
    empty_keys = describe_empty_citekeys(library)
    if empty_keys:
        raise _malformed_bib_refusal(empty_keys[0])
    # Same reasoning as a mangled key, one step earlier: the case-fold at the
    # parse boundary has already discarded one of the two values by the time any
    # write gate runs, so the round-trip check cannot see the loss.
    case_collisions = describe_case_colliding_field_keys(library)
    if case_collisions:
        raise _malformed_bib_refusal(case_collisions[0])
    if not library.failed_blocks:
        return
    # `describe_failed_blocks` names the citekey for a duplicate and adds the
    # 1-based line; reuse it rather than re-deriving a worse message. The old
    # text interpolated the raw 0-based `start_line`, so a duplicate on line 4
    # was reported as "around line 3".
    detail = describe_failed_blocks(library)[0]
    raise _malformed_bib_refusal(detail)


def _malformed_bib_refusal(detail: str) -> PziError:
    """The user has to fix the file by hand, so say so — do not raise a traceback.

    These messages were always written for a reader ("refusing to rewrite the
    file"), but as a bare ``ValueError`` they reached that reader as a Python
    stack trace with exit 1, and ``--json`` printed nothing at all.
    """
    return PziError(
        f"malformed BibTeX: refusing to rewrite the file — {detail}",
        code=exit_codes.ENVIRONMENT,
    )


def library_to_entries_records(
    library: Library, bib_path: str
) -> tuple[list[BibtexEntry], list[NormalizedRecord]]:
    """Extract entries and normalized records from a v2 Library."""
    entries = [library_entry_to_bibtex_entry(e) for e in library.entries]
    records: list[NormalizedRecord] = [bibtex_entry_to_record(e) for e in entries]
    for record, entry in zip(records, entries):
        resolve_file_field(record, entry, bib_path)
    return entries, records


def serialize_library(library: Library, *, layout: BibLayout | None) -> str:
    """Serialize a v2 Library to BibTeX text, preserving enclosings and layout.

    ``parse_bib_library`` strips enclosings with ``RemoveEnclosingMiddleware``,
    which records what it removed (including ``no-enclosing`` for a bare
    ``@string`` macro reference) on each block. The writer's *default* unparse
    stack discards that record and braces everything, which turns
    ``journal = jmlr`` into ``journal = {jmlr}`` — the ``@string`` block survives
    but every reference to it is severed. Reusing the recorded enclosing writes
    those fields back exactly as they were found.

    This only helps blocks that still carry the parser's record. Entries a write
    plan rebuilt (see ``bibtex_entry_to_library_entry``) carry none, so they
    fall back to ``default_enclosing`` and are brace-wrapped. That is also what
    keeps ``_safe_field_value``'s injection guard meaningful: every value this
    code composes is enclosed, and only text read verbatim off disk is written
    back bare.

    A rebuilt entry keeps the source text of the fields a write did not change,
    via :func:`merge_preserving_unchanged_source`, so its own untouched macro
    references survive too.

    *layout* carries the file's own indent, trailing-comma and block-separator
    conventions (:func:`detect_bib_layout`). It is a **required keyword** with no
    default, and ``None`` is the explicit "there is no source file to match"
    answer: passing it must be a decision at every call site, because getting it
    wrong is the difference between a one-entry diff and a whole-file reformat,
    and the previous version of this bug was invisible for four reviews.
    """
    layout = layout or BibLayout()
    fmt = BibtexFormat()
    fmt.indent = layout.indent
    fmt.trailing_comma = layout.trailing_comma
    # Blocks are joined here, not by the writer. bibtexparser appends
    # `block_separator` after *every* block regardless of what follows, which
    # cannot express a file that separates its entries by a blank line while
    # keeping its `% ==` comments flush against the entry they describe — and
    # that is what a Better BibTeX export looks like. Writing each block with no
    # separator and joining by the *following* block's kind reproduces both.
    # Safe to write per block because `value_column` is 0 (bibtexparser's
    # default): no alignment is computed across the library, so a block's
    # rendering does not depend on its neighbours.
    fmt.block_separator = ""
    unparse_stack = [
        AddEnclosingMiddleware(
            default_enclosing="{",
            reuse_previous_enclosing=True,
            enclose_integers=True,
        )
    ]
    pieces: list[str] = []
    for position, block in enumerate(library.blocks):
        if position:
            pieces.append(
                layout.comment_separator
                if isinstance(block, ImplicitComment | ExplicitComment)
                else layout.block_separator
            )
        pieces.append(
            write_string(
                Library(blocks=[block]),
                unparse_stack=unparse_stack,
                bibtex_format=fmt,
            )
        )
    return "".join(pieces)


def validate_bibtex_roundtrip(entries: list[BibtexEntry]) -> None:
    """Raise if entries cannot survive a serialize→parse round-trip.

    A :exc:`PziError` from :func:`build_library` (a duplicate citekey) already
    says what is wrong in the user's own terms, so it passes through untouched;
    anything else is a library-internal failure and gets the generic wrapper.

    Parsing without raising proves nothing: bibtexparser v2 *collects* a block it
    cannot read in ``failed_blocks`` and drops it from ``entries``, so text that
    reparses to zero entries and one failure returns normally. This is the sole
    gate in front of every write sink, so it checks the parse *result* — no
    failed blocks, the same number of entries, and the same
    ``(entry_type, citekey, fields)`` — rather than the absence of an exception.
    Values are compared *after* sanitizing, because sanitizing is a deliberate
    rewrite; what must not change is anything the serializer itself did.
    """
    try:
        blocks = [bibtex_entry_to_library_entry(entry) for entry in entries]
        text = _write_library_text(build_library(blocks))
        reparsed = parse_string(text)
    except PziError:
        raise
    except Exception as exc:
        raise PziError(
            f"write plan produces invalid BibTeX: {exc}",
            code=exit_codes.ENVIRONMENT,
        ) from exc
    _assert_roundtrip_is_faithful(blocks, reparsed)


def _roundtrip_refusal(detail: str) -> PziError:
    return PziError(
        f"write plan produces invalid BibTeX: {detail}", code=exit_codes.ENVIRONMENT
    )


def _comparable_entry(entry: BibtexEntryV2) -> tuple[str, str, dict[str, str]]:
    """The part of an entry a round-trip must preserve exactly."""
    return (
        entry.entry_type,
        entry.key,
        {field.key.lower(): field.value for field in entry.fields},
    )


def _assert_roundtrip_is_faithful(
    written: list[BibtexEntryV2], reparsed: Library
) -> None:
    failures = describe_failed_blocks(reparsed)
    if failures:
        raise _roundtrip_refusal(failures[0])
    got = reparsed.entries
    if len(got) != len(written):
        raise _roundtrip_refusal(
            f"{len(written)} entries were written but {len(got)} parsed back"
        )
    for expected, actual in zip(written, got):
        if _comparable_entry(actual) != _comparable_entry(expected):
            raise _roundtrip_refusal(
                f"entry {expected.key!r} does not read back as it was written"
            )


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

    *rebuilt* owns which fields exist at all, so a plan can still add and remove
    them. Everything else about how they were written is *original*'s: fields it
    already had keep their position, their key's capitalization and their source
    text, and fields the plan added are appended after them. A rebuilt block is
    alphabetized and lowercased, so emitting it as-is reordered and re-cased
    every field of every touched entry — a library edited by pzi slowly drifted
    into a second convention, one entry at a time.
    """
    original_fields = {field.key.lower(): field for field in original.fields}
    original_order = {
        field.key.lower(): position for position, field in enumerate(original.fields)
    }
    original_enclosing = original.parser_metadata.get("removed_enclosing", {})
    if not isinstance(original_enclosing, dict):  # pragma: no cover — defensive
        original_enclosing = {}

    known: list[tuple[int, Field]] = []
    added: list[Field] = []
    preserved_enclosing: dict[str, str] = {}
    for field in rebuilt.fields:
        source_field = original_fields.get(field.key.lower())
        if source_field is None:
            added.append(field)
            continue
        position = original_order[field.key.lower()]
        # `removed_enclosing` is keyed by the field key as it was written, so
        # every lookup and every key emitted below uses the source spelling.
        enclosing = original_enclosing.get(source_field.key)
        unchanged = field.value in _unchanged_forms(
            source_field.value, enclosing, strings
        ) or (
            field.key.lower() == "file"
            and _file_field_still_points_at(source_field.value, field.value)
        )
        if not unchanged:
            known.append((position, Field(key=source_field.key, value=field.value)))
            continue
        known.append((position, source_field))
        if isinstance(enclosing, str):
            preserved_enclosing[source_field.key] = enclosing

    fields: list[Field] = [
        field for _position, field in sorted(known, key=lambda item: item[0])
    ] + added

    merged = BibtexEntryV2(
        entry_type=rebuilt.entry_type, key=rebuilt.key, fields=fields,
    )
    # `parser_metadata` is bibtexparser's own (experimental) channel for this;
    # `serialize_library`'s AddEnclosingMiddleware reads exactly this key to
    # decide which fields to write back unenclosed. Only preserved fields get an
    # entry, so every rebuilt value falls through to the default `{` enclosing.
    if preserved_enclosing:
        merged.parser_metadata["removed_enclosing"] = preserved_enclosing
    return merged


def _file_field_still_points_at(source_value: str, rebuilt_value: str) -> bool:
    """Rebuilt ``file`` values that still mean *source_value*'s attachment.

    A Zotero/JabRef composite (``description:path:mimetype``, several joined by
    ``;``) enters the record model as a bare path, so the rebuilt block always
    disagrees with the source text and the composite — the description, the
    mimetype, and every attachment after the first — was overwritten by any
    command that touched the entry, `tag add` included.

    pzi does not re-compose one: that would mean owning three producers'
    escaping rules to gain nothing, since a bare path is the one form all of
    them read. Instead the field counts as unchanged while it still points at
    the same attachment, and the source text is kept verbatim.

    The rebuilt value is derived *from* this source by `resolve_file_field`, so
    it is either the primary path itself (absolute) or the bib directory joined
    to it (relative) — which is why no bib path is needed here. A `..` segment
    could defeat the suffix test; that fails safe, rewriting to a bare path,
    which is today's behaviour.
    """
    primary = primary_pdf_path(source_value)
    if primary is None:
        return False
    if primary == source_value.strip():
        # A bare path. It still counts as unchanged when it is *relative* and
        # the rebuilt absolute path names the same file, because `file` is read
        # into the record model as an absolute `local_pdf_path` and written back
        # absolute — so any command that touched an entry rewrote a portable
        # `papers/x.pdf` into a machine-specific `/home/you/bibs/papers/x.pdf`.
        # Reproduced with `tag add`: one tagged entry became absolute while
        # every untouched entry stayed relative, i.e. a git-tracked library
        # drifted to machine-specific one entry at a time, silently.
        #
        # This preserves what the entry already had rather than imposing a
        # style, the same principle as sniffing the file's layout: it is not
        # `pdf_file_path_style`'s job to decide what an *existing* field looks
        # like. That setting still decides what a newly attached PDF is written
        # as.
        if not primary.startswith(("/", "~")) and rebuilt_value.endswith(
            "/" + str(Path(primary))
        ):
            return True
        return False  # already a bare absolute path; the normal comparison applies
    if rebuilt_value == primary:
        return True
    if not primary.startswith(("/", "~")):
        # Relative primary: `resolve_file_field` joined the bib directory to it.
        return rebuilt_value.endswith("/" + str(Path(primary)))
    # Absolute primary under `pdf_file_path_style = "relative"`:
    # `_normalize_file_field` shortened the rebuilt value against the bib dir.
    return bool(rebuilt_value) and primary.endswith("/" + str(Path(rebuilt_value)))


def _unchanged_forms(
    value: str, enclosing: object, strings: Mapping[str, str]
) -> frozenset[str]:
    """Every value a caller could hold that means "this field is as on disk".

    Two parse stacks feed write plans and they disagree about macros, so both
    readings have to count as unchanged:

    - ``parse_bibtex`` (``read_bib_file``, and so every service that plans from
      records) *resolves* a bare ``@string`` reference, yielding the definition.
    - ``parse_bib_library`` (``update_bib_entry``, ``merge_bib_entries``) does
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


def library_entry_to_bibtex_entry(entry: BibtexEntryV2) -> BibtexEntry:
    """Convert a bibtexparser v2 Entry to the internal BibtexEntry dict.

    Field keys are case-folded here, at the single parse boundary. BibTeX field
    names are case-insensitive and bibtexparser lowercases the entry *type* but
    not the keys, so a JabRef/IEEE-style ``Author =`` / ``Title =`` / ``Doi =``
    / ``File =`` was invisible to the whole record model: dedup missed the DOI
    and added a second copy, an update wrote lowercase twins *alongside* the
    capitalized originals, ``library clean`` quarantined a referenced PDF, and
    ``pzi entries`` rendered the entry blank.

    The user's own spelling is not lost — it is restored on write by
    :func:`merge_preserving_unchanged_source` for every entry that already
    existed on disk.
    """
    return {
        "entry_type": entry.entry_type,
        "citekey": entry.key,
        "fields": {f.key.lower(): f.value for f in entry.fields},
    }


# Citekeys are written as ``@type{<key>,`` (unquoted), and field values as
# ``{<value>}``.  Untrusted metadata (a hostile capture page, a crafted
# ``--citekey``, a malicious ``--metadata-json``) could otherwise
# break out of those delimiters and inject or corrupt entries, so both are
# neutralized where a citekey is *composed* (:func:`_safe_citekey`, used by
# citekey generation and by the add path's explicit-citekey branch) and checked
# again where any entry is serialized (:func:`_checked_citekey`).
#
# ``/`` is intentionally excluded from a composed key: a citekey doubles as the
# PDF filename stem, so a path separator there has no legitimate use and would
# be one more way to smuggle path components toward the filesystem (paths are
# also basename-guarded downstream — this removes it at the source).
_UNSAFE_CITEKEY = re.compile(r"[^A-Za-z0-9_:.+\-]")
# What actually breaks out of ``@type{<key>,``. Verified against the parser:
# ``{``, ``,``, ``=`` and ``"`` make the block unparseable, and ``}`` silently
# truncates the key — so a citekey read off disk can never contain them, and
# refusing them costs a real library nothing. Everything the parser does accept
# (``ü``, ``&``, ``'``, ``(``, ``%``, ``#``, ``\``, even a space) is written
# back exactly as the user typed it.
_STRUCTURAL_CITEKEY = re.compile(r"[{},=\"\r\n\x00-\x1f\x7f]")
_UNSAFE_ENTRY_TYPE = re.compile(r"[^A-Za-z]")
# Control characters (keep \t and \n) — NUL and friends have no place in a
# BibTeX field value and can corrupt the file or downstream tools.
#
# `\r` (\x0d) is stripped too, and deliberately: line endings are the file's
# property, applied once by `_write_bib_text_atomic`, which rewrites every `\n`
# as the file's own newline. A `\r` surviving inside a value therefore became
# `\r\r\n` on a CRLF file. Values read from disk never contain one — `read_text`
# translates newlines — so this only ever arrived with text injected from a
# metadata provider, which is also why `validate_bibtex_roundtrip` could not
# catch it: that runs on the LF text, before the newline conversion.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f]")


def _safe_citekey(citekey: str) -> str:
    """Strip characters that could escape the ``@type{<key>,`` context.

    For citekeys the *code* composes — generated from metadata, or supplied as
    ``--citekey`` / ``citekey`` in a capture payload. Never apply this to a key
    read off disk: rewriting ``@article{Müller2020}`` to ``@article{Mller2020}``
    silently breaks every ``\\cite{Müller2020}`` in the user's LaTeX.
    """
    cleaned = _UNSAFE_CITEKEY.sub("", citekey).strip(".")
    return cleaned or "untitled"


#: Public name for the composed-citekey sanitizer — callers outside this module
#: (the add path's explicit-citekey branch) should not reach for the private one.
safe_composed_citekey = _safe_citekey


def _checked_citekey(citekey: str) -> str:
    """Pass a citekey through, refusing one that would corrupt the file.

    The serialization backstop for keys this code did not compose. An on-disk
    key is returned verbatim; a key carrying a delimiter (which can only have
    come from code or from untrusted input that skipped
    :func:`_safe_citekey`) is a loud refusal, never a silent rewrite.
    """
    key = citekey.strip()
    if not key:
        raise PziError(
            "refusing to write an entry with an empty citekey",
            code=exit_codes.ENVIRONMENT,
        )
    match = _STRUCTURAL_CITEKEY.search(key)
    if match:
        raise PziError(
            f"refusing to write citekey {citekey!r}: "
            f"{match.group()!r} cannot appear in a BibTeX entry key",
            code=exit_codes.ENVIRONMENT,
        )
    return key


def _safe_field_value(value: str) -> str:
    """Make an untrusted field value safe to serialize inside ``{...}``."""
    return _strip_trailing_backslashes(_balance_braces(_CONTROL_CHARS.sub("", value)))


#: A backslash run at the very end of a value, which would escape the closing
#: brace this code is about to write.
_TRAILING_BACKSLASHES = re.compile(r"\\+\Z")


def _strip_trailing_backslashes(value: str) -> str:
    """Drop a backslash run at the end of a value.

    Written as ``{<value>}`` the run swallows the writer's own closing brace.
    bibtexparser's splitter looks only at the single character preceding a
    ``}``, so an *even* run (a legitimately escaped backslash) breaks the block
    exactly as an odd one does — the entry then vanishes from every read and
    every later write to that library is refused.

    A backslash at the very end has nothing to escape, so removing it loses no
    meaning. The usual source is not a hostile value at all: ``\\ `` is LaTeX's
    forced inter-word space, and the read side strips the space off the end,
    leaving the backslash exposed.

    Runs before an *internal* ``}`` are untouched — :func:`_balance_braces`
    keeps those, and this runs after it so a brace it dropped cannot leave a
    fresh backslash at the end.
    """
    return _TRAILING_BACKSLASHES.sub("", value)


#: What a BibTeX field name may contain: word characters — Unicode letters,
#: digits and underscore — plus the punctuation real-world keys use
#: (``bdsk-url-1``, ``date-added``, ``__markedentry``).
#:
#: `\w`, not ``A-Za-z0-9_``. The ASCII-only form refused keys that are perfectly
#: legal biblatex — a Swedish ``författare-not``, a French ``année`` — and the
#: cost was not local: this gate runs over the *whole library* on every write,
#: so one such key anywhere made every `import`, `update` and `tag` fail, naming
#: an entry the user had not touched. It also made a batch dry run disagree with
#: the real run, since only the real write validates the whole library.
#:
#: Still deliberately narrow. A space, ``/``, ``#`` or ``@`` is refused: those
#: either break real BibTeX readers or could escape the ``key = {value}``
#: structure the serializer writes. bibtexparser round-trips ``a b`` happily,
#: but that is leniency, not legality.
_SAFE_FIELD_KEY = re.compile(r"\A[\w:.+-]+\Z")


def _checked_field_key(key: str, citekey: str) -> str:
    """Refuse a field name that would be written as something other than a name.

    A citekey has been checked at this chokepoint for a while; a *field* key was
    written verbatim, so `pzi import` could carry a foreign entry's mangled key
    into the user's library. Two ways that goes wrong, both silent:

    * ``ti,tle`` / ``ti{tle`` make the block unparseable and ``ti}tle`` parses to
      an entry with **zero** fields — total field loss on the next read;
    * a ``%`` comment inside an entry is folded by the parser into the following
      field's key (``'% private note\\n  doi'``). That round-trips perfectly, so
      the write gate sees nothing wrong, while the hidden field's value is
      attached to a key no reader will ever match and every subsequent write to
      the library is refused.

    Refusing is right rather than sanitizing: silently renaming the key would
    move the user's data somewhere they did not put it.
    """
    if _SAFE_FIELD_KEY.match(key):
        return key
    if _MANGLED_FIELD_KEY.search(key):
        hidden = key.rsplit("\n", 1)[-1].strip()
        raise PziError(
            f"refusing to write entry {citekey!r}: a '%' comment inside the entry "
            f"was folded into the field name {hidden!r}, which would hide it from "
            "every reader — move the comment outside the entry",
            code=exit_codes.ENVIRONMENT,
        )
    raise PziError(
        f"refusing to write entry {citekey!r}: {key!r} is not a usable BibTeX "
        "field name",
        code=exit_codes.ENVIRONMENT,
    )


def _escaped_positions(value: str) -> list[bool]:
    """Per-character flag: is this character escaped by a preceding backslash?

    A brace preceded by an *odd* run of backslashes is literal text (``\\}``),
    not a delimiter. Counting the run rather than looking at the single previous
    character keeps ``\\\\}`` — an escaped backslash followed by a real closing
    brace — reading as a delimiter.
    """
    flags: list[bool] = []
    run = 0
    for ch in value:
        flags.append(run % 2 == 1)
        run = run + 1 if ch == "\\" else 0
    return flags


def _balance_braces(value: str) -> str:
    """Drop unmatched braces so a field value cannot terminate its ``{...}``.

    Balanced groups (e.g. case protection like ``{DNA}``) are preserved; only
    stray ``}`` (which would end the field early) and stray ``{`` are removed.
    LaTeX-escaped braces are left alone: an escape-blind counter read ``\\}`` as
    an unmatched closer and deleted it, mangling ``note = {a \\} b}`` on any
    write that touched a neighbouring field.
    """
    if "{" not in value and "}" not in value:
        return value
    escaped = _escaped_positions(value)
    kept: list[tuple[str, bool]] = []
    depth = 0
    for ch, is_escaped in zip(value, escaped):  # left-to-right: drop unmatched `}`
        if not is_escaped:
            if ch == "}":
                if depth == 0:
                    continue
                depth -= 1
            elif ch == "{":
                depth += 1
        kept.append((ch, is_escaped))
    out: list[str] = []
    depth = 0
    for ch, is_escaped in reversed(kept):  # right-to-left: drop unmatched `{`
        if not is_escaped:
            if ch == "{":
                if depth == 0:
                    continue
                depth -= 1
            elif ch == "}":
                depth += 1
        out.append(ch)
    return "".join(reversed(out))


def bibtex_entry_to_library_entry(
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
        key=_checked_citekey(entry["citekey"]),
        fields=[
            Field(key=_checked_field_key(k, entry["citekey"]), value=_safe_field_value(v))
            for k, v in sorted(entry["fields"].items())
        ],
    )
