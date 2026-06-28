"""Pure BibTeX serialization: text↔model↔dict conversion and injection-safety.

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
from pathlib import Path

from bibtexparser.entrypoint import parse_string, write_string
from bibtexparser.library import Library
from bibtexparser.middlewares.enclosing import RemoveEnclosingMiddleware
from bibtexparser.model import Entry as BibtexEntryV2
from bibtexparser.model import Field
from bibtexparser.writer import BibtexFormat

from pzi.bibtex import BibtexEntry, NormalizedRecord, bibtex_entry_to_record


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


def _validate_bibtex_roundtrip(entries: list[BibtexEntry]) -> None:
    """Raise ValueError if entries cannot survive a serialize→parse round-trip."""
    try:
        text = serialize_bibtex(entries)
        parse_bibtex(text)
    except Exception as exc:
        raise ValueError(
            f"write plan produces invalid BibTeX: {exc}"
        ) from exc


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
