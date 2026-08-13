"""Export BibTeX library to CSV, JSON, RIS, and BibTeX formats."""

from __future__ import annotations

import csv
import io
import json as _json
from pathlib import Path
from typing import Any, TypedDict

from pzi.bib_repository import (
    ReadBibResult,
    parse_bib_library,
    read_bib_file_raw_with_failures,
    read_bib_source,
    with_bib_lock,
)
from pzi.bib_serialize import (
    describe_failed_blocks,
    detect_bib_layout,
    library_entry_to_bibtex_entry,
    serialize_library,
)


class ExportResult(TypedDict):
    status: str
    bib_path: str
    total_entries: int
    format: str
    content: str
    content_type: str
    errors: list[str]

# RIS type mapping from bibtex entry types
_BIBTEX_TO_RIS_TYPE: dict[str, str] = {
    "article": "JOUR",
    "inproceedings": "CONF",
    "conference": "CONF",
    "book": "BOOK",
    "incollection": "CHAP",
    "inbook": "CHAP",
    "phdthesis": "THES",
    "mastersthesis": "THES",
    "techreport": "RPRT",
    "misc": "GEN",
    "unpublished": "UNPB",
}

# RIS field tag mapping
_RIS_FIELDS: list[tuple[str, str]] = [
    # (normalized_record_key, ris_tag)
    # title (TI) is emitted explicitly before this loop, so it is not listed here.
    ("venue", "T2"),  # journal/booktitle → secondary title
    ("doi", "DO"),
    # canonical_url is not listed: it is emitted with the other URLs below, so
    # that a record whose canonical_url and source_url hold the same string
    # (which is what bibtex.py produces from a single `url` field) yields one
    # UR line rather than two.
    ("year", "PY"),
    ("abstract", "AB"),
    # The bibliographic detail a journal style needs to render a citation. RIS
    # has a standard tag for every one of these, and RIS exists to hand a
    # citation to another reference manager — so an export missing them arrives
    # as a citation that cannot be rendered. `export_json` emitted all six from
    # the day the records gained them; CSV and RIS did not, which made a backup
    # taken in two of the four formats lossy.
    # `pages` is handled below: RIS splits it into SP/EP.
    ("volume", "VL"),
    ("number", "IS"),
    ("publisher", "PB"),
    ("issn", "SN"),
    ("isbn", "SN"),
    ("note", "N1"),
    ("citekey", "ID"),  # custom: citekey as reference ID
]

_CSV_HEADERS = [
    "citekey", "entry_type", "title", "authors", "year",
    "venue", "doi", "arxiv_id", "canonical_url", "local_pdf_path",
    "abstract", "tags", "note",
    "volume", "number", "pages", "publisher", "issn", "isbn",
]


def _normalize_authors(authors: object) -> str:
    """Join author list into semicolon-separated string."""
    if isinstance(authors, list):
        return "; ".join(str(a) for a in authors)
    if isinstance(authors, str):
        return authors
    return ""


def _normalize_tags(tags: object) -> str:
    """Join tags list into comma-separated string."""
    if isinstance(tags, list):
        return ", ".join(str(t) for t in tags)
    if isinstance(tags, str):
        return tags
    return ""


def _missing_bib_errors(bib_path: str) -> list[str]:
    """The configured bib not existing, as an export error rather than a warning.

    Reading treats a missing bib as a warning on purpose (see
    :func:`bib_repository.describe_missing_bib`): a freshly ``pzi init``-ed
    config names a bib that does not exist until the first ``add``. Export
    cannot afford that leniency, because its output *replaces* something — a
    renamed file, an unmounted share or a typo'd ``path =`` made
    ``pzi export --force -o backup.bib`` truncate the backup to zero bytes and
    report "exported 0 entries", exit 0.
    """
    if Path(bib_path).exists():
        return []
    return [f"bib file does not exist: {bib_path}"]


def _read_for_export(bib_path: str) -> tuple[ReadBibResult, list[str]]:
    with with_bib_lock(bib_path, shared=True):
        raw, dropped = read_bib_file_raw_with_failures(bib_path)
    return raw, [*_missing_bib_errors(bib_path), *dropped]


def _export_status(dropped: list[str]) -> str:
    """An export that silently omits entries is not an ``ok`` export.

    These commands are the documented way to back a library up, so losing
    entries to a lenient parse has to be loud: the caller compares counts, or
    keeps the file, believing it holds everything.
    """
    return "ok" if not dropped else "error"


def export_bibtex(bib_path: str) -> ExportResult:
    """Export a BibTeX library as formatted BibTeX text string.

    Re-serializes the parsed library rather than the projected entry list.
    Building a fresh library out of entries alone dropped every non-entry block
    — ``@preamble``, ``@string``, ``@comment`` and every ``%`` comment — and,
    because the entry list carries resolved values with no record of their
    enclosing, turned ``publisher = acm # { Press}`` into the literal
    ``{acm # { Press}}``. For a command billed as a backup that is data loss.
    """
    with with_bib_lock(bib_path, shared=True):
        source = read_bib_source(bib_path)
        library = parse_bib_library(source)
        entries = [
            library_entry_to_bibtex_entry(entry) for entry in library.entries
        ]
        dropped = [*_missing_bib_errors(bib_path), *describe_failed_blocks(library)]
        # Sniffed from the library being exported, for the reason above: a
        # BibTeX export is billed as a backup, and a backup that comes back
        # re-indented with its trailing commas stripped is a diff against the
        # thing it was meant to reproduce.
        bibtex_str = serialize_library(library, layout=detect_bib_layout(source))
    return {
        "status": _export_status(dropped),
        "bib_path": bib_path,
        "total_entries": len(entries),
        "format": "bibtex",
        "content": bibtex_str,
        "content_type": "application/x-bibtex",
        "errors": dropped,
    }


def export_json(bib_path: str) -> ExportResult:
    """Export a BibTeX library as formatted JSON string."""
    raw, dropped = _read_for_export(bib_path)
    records = raw["records"]
    # Include entry_type from corresponding entry
    entries = raw["entries"]
    json_records: list[dict[str, Any]] = []
    for i, record in enumerate(records):
        item = dict(record)
        if i < len(entries):
            item["entry_type"] = entries[i].get("entry_type", "article")
        json_records.append(item)
    json_str = _json.dumps(json_records, indent=2, default=str, ensure_ascii=False)
    return {
        "status": _export_status(dropped),
        "bib_path": bib_path,
        "total_entries": len(records),
        "format": "json",
        "content": json_str,
        "content_type": "application/json",
        "errors": dropped,
    }


#: Characters a spreadsheet treats as the start of a formula. A title that
#: begins with one — legitimately, or because someone put it there — is executed
#: on open in Excel/LibreOffice/Sheets rather than displayed.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> object:
    """Neutralize a cell a spreadsheet would evaluate as a formula."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def export_csv(bib_path: str) -> ExportResult:
    """Export a BibTeX library as CSV string.

    Cells that would be read as formulas are prefixed with an apostrophe, the
    convention spreadsheets understand as "this is text".
    """
    raw, dropped = _read_for_export(bib_path)
    records = raw["records"]
    entries = raw["entries"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADERS)

    for i, record in enumerate(records):
        entry_type = entries[i].get("entry_type", "article") if i < len(entries) else "article"
        row = [
            record.get("citekey", ""),
            entry_type,
            record.get("title", ""),
            _normalize_authors(record.get("authors")),
            record.get("year", ""),
            record.get("venue", ""),
            record.get("doi", ""),
            record.get("arxiv_id", ""),
            record.get("canonical_url", ""),
            record.get("local_pdf_path", ""),
            record.get("abstract", ""),
            _normalize_tags(record.get("tags")),
            record.get("note", ""),
            record.get("volume", ""),
            record.get("number", ""),
            record.get("pages", ""),
            record.get("publisher", ""),
            record.get("issn", ""),
            record.get("isbn", ""),
        ]
        # The row is built positionally, so it has to stay the same length as
        # the header — adding a column to one and not the other writes a CSV
        # whose values are under the wrong names.
        assert len(row) == len(_CSV_HEADERS), (len(row), len(_CSV_HEADERS))
        writer.writerow([_csv_safe(cell) for cell in row])

    return {
        "status": _export_status(dropped),
        "bib_path": bib_path,
        "total_entries": len(records),
        "format": "csv",
        "content": buf.getvalue(),
        "content_type": "text/csv",
        "errors": dropped,
    }


def _ris_value(value: object) -> str:
    """One RIS line's worth of text: whitespace collapsed to single spaces.

    RIS has no continuation syntax — every line must be ``XX  - value``. A
    wrapped BibTeX abstract carries newlines straight through, emitting bare
    untagged lines that tolerant readers glue onto the previous field and
    strict ones drop or mis-assign. Worse, a continuation line beginning with
    two characters and ``  - `` is silently reparsed as a new field.
    """
    return " ".join(str(value).split())


def export_ris(bib_path: str) -> ExportResult:
    """Export a BibTeX library as RIS formatted text string."""
    raw, dropped = _read_for_export(bib_path)
    records = raw["records"]
    entries = raw["entries"]

    lines: list[str] = []
    for i, record in enumerate(records):
        # Determine RIS type
        entry_type = entries[i].get("entry_type", "article") if i < len(entries) else "article"
        ris_type = _BIBTEX_TO_RIS_TYPE.get(entry_type, "JOUR")

        lines.append(f"TY  - {ris_type}")

        # Title
        title = record.get("title")
        if title:
            lines.append(f"TI  - {_ris_value(title)}")

        # Authors
        authors = record.get("authors")
        if isinstance(authors, list):
            for author in authors:
                lines.append(f"AU  - {_ris_value(author)}")
        elif isinstance(authors, str) and authors.strip():
            # BibTeX "and"-separated → split
            for author in authors.split(" and "):
                if author.strip():
                    lines.append(f"AU  - {_ris_value(author)}")

        # Mapped fields
        for field_key, ris_tag in _RIS_FIELDS:
            if field_key == "citekey":
                value = record.get(field_key, "")
            else:
                value = record.get(field_key)
            if value is not None and (not isinstance(value, str) or value.strip()):
                lines.append(f"{ris_tag}  - {_ris_value(value)}")

        # Pages, which RIS splits across a start and an end tag rather than
        # carrying BibTeX's `123--145` range in one field.
        pages = record.get("pages")
        if isinstance(pages, str) and pages.strip():
            start, _, end = pages.replace("--", "-").partition("-")
            if start.strip():
                lines.append(f"SP  - {_ris_value(start.strip())}")
            if end.strip():
                lines.append(f"EP  - {_ris_value(end.strip())}")

        # Tags as KW
        tags = record.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                lines.append(f"KW  - {_ris_value(tag)}")
        elif isinstance(tags, str) and tags.strip():
            for tag in tags.split(","):
                tag = tag.strip()
                if tag:
                    lines.append(f"KW  - {_ris_value(tag)}")

        # Local PDF as L1
        local_pdf = record.get("local_pdf_path")
        if local_pdf:
            lines.append(f"L1  - file://{_ris_value(local_pdf)}")

        # URLs, deduplicated in a stable order. canonical_url and source_url are
        # both populated from the single BibTeX `url` field, so emitting them
        # independently produced a duplicate UR line for every entry read from a
        # bib file.
        arxiv = record.get("arxiv_id")
        urls = [
            _ris_value(candidate)
            for candidate in (
                record.get("canonical_url"),
                f"https://arxiv.org/abs/{arxiv}" if arxiv else None,
                record.get("source_url"),
            )
            if candidate
        ]
        for url in dict.fromkeys(urls):
            lines.append(f"UR  - {url}")

        lines.append("ER  - ")
        lines.append("")  # blank line between entries

    ris_str = "\n".join(lines)
    return {
        "status": _export_status(dropped),
        "bib_path": bib_path,
        "total_entries": len(records),
        "format": "ris",
        "content": ris_str,
        "content_type": "application/x-research-info-systems",
        "errors": dropped,
    }
