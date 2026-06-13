"""Export BibTeX library to CSV, JSON, RIS, and BibTeX formats."""

from __future__ import annotations

import csv
import io
import json as _json
from typing import Any, TypeAlias

from pzi.bib_repository import _read_bib_file_raw, serialize_bibtex, with_bib_lock

ExportResult: TypeAlias = dict[str, Any]

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
    ("title", "TI"),
    ("venue", "T2"),  # journal/booktitle → secondary title
    ("doi", "DO"),
    ("canonical_url", "UR"),
    ("year", "PY"),
    ("abstract", "AB"),
    ("note", "N1"),
    ("citekey", "ID"),  # custom: citekey as reference ID
]

_CSV_HEADERS = [
    "citekey", "entry_type", "title", "authors", "year",
    "venue", "doi", "arxiv_id", "canonical_url", "local_pdf_path",
    "abstract", "tags", "note",
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


def export_bibtex(bib_path: str) -> ExportResult:
    """Export a BibTeX library as formatted BibTeX text string."""
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
    entries = raw["entries"]
    bibtex_str = serialize_bibtex(entries)
    return {
        "status": "ok",
        "bib_path": bib_path,
        "total_entries": len(entries),
        "format": "bibtex",
        "content": bibtex_str,
        "content_type": "application/x-bibtex",
        "errors": [],
    }


def export_json(bib_path: str) -> ExportResult:
    """Export a BibTeX library as formatted JSON string."""
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
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
        "status": "ok",
        "bib_path": bib_path,
        "total_entries": len(records),
        "format": "json",
        "content": json_str,
        "content_type": "application/json",
        "errors": [],
    }


def export_csv(bib_path: str) -> ExportResult:
    """Export a BibTeX library as CSV string."""
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
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
        ]
        writer.writerow(row)

    return {
        "status": "ok",
        "bib_path": bib_path,
        "total_entries": len(records),
        "format": "csv",
        "content": buf.getvalue(),
        "content_type": "text/csv",
        "errors": [],
    }


def export_ris(bib_path: str) -> ExportResult:
    """Export a BibTeX library as RIS formatted text string."""
    with with_bib_lock(bib_path, shared=True):
        raw = _read_bib_file_raw(bib_path)
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
            lines.append(f"TI  - {title}")

        # Authors
        authors = record.get("authors")
        if isinstance(authors, list):
            for author in authors:
                lines.append(f"AU  - {author}")
        elif isinstance(authors, str) and authors.strip():
            # BibTeX "and"-separated → split
            for author in authors.split(" and "):
                if author.strip():
                    lines.append(f"AU  - {author.strip()}")

        # Mapped fields
        for field_key, ris_tag in _RIS_FIELDS:
            if field_key == "citekey":
                value = record.get(field_key, "")
            else:
                value = record.get(field_key)
            if value is not None and (not isinstance(value, str) or value.strip()):
                lines.append(f"{ris_tag}  - {value}")

        # Tags as KW
        tags = record.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                lines.append(f"KW  - {tag}")
        elif isinstance(tags, str) and tags.strip():
            for tag in tags.split(","):
                tag = tag.strip()
                if tag:
                    lines.append(f"KW  - {tag}")

        # ArXiv ID
        arxiv = record.get("arxiv_id")
        if arxiv:
            lines.append(f"UR  - https://arxiv.org/abs/{arxiv}")

        # Local PDF as L1
        local_pdf = record.get("local_pdf_path")
        if local_pdf:
            lines.append(f"L1  - file://{local_pdf}")

        # Source URL
        source = record.get("source_url")
        if source:
            lines.append(f"UR  - {source}")

        lines.append("ER  - ")
        lines.append("")  # blank line between entries

    ris_str = "\n".join(lines)
    return {
        "status": "ok",
        "bib_path": bib_path,
        "total_entries": len(records),
        "format": "ris",
        "content": ris_str,
        "content_type": "application/x-research-info-systems",
        "errors": [],
    }
