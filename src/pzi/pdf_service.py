"""PDF retry, attach, and metadata extraction services."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi.bib_repository import _find_entry_index, read_bib_file, update_bib_entry
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    extract_note_field,
    record_to_bibtex_entry,
)
from pzi.config import load_and_resolve_bib
from pzi.pdf import fetch_and_store_pdf, store_pdf_source, write_pdf_bytes

PdfRetryResult: TypeAlias = dict[str, Any]



PdfAttachResult: TypeAlias = dict[str, Any]



PdfAttachBytesResult: TypeAlias = dict[str, Any]



def retry_pdf(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    fetch_binary=None,
) -> PdfRetryResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "could not resolve target bib",
            "warnings": [],
            "errors": resolved,
        }
    _config, bib = resolved

    read_result = read_bib_file(bib["path"])
    entries = read_result["entries"]
    index = _find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "citekey not found",
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    raw_note = entries[index]["fields"].get("note")
    pdf_url = extract_note_field(raw_note, "PDF")
    if pdf_url is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "no PDF URL on entry",
            "warnings": [],
            "errors": ["no PDF URL found in note field"],
        }

    local_pdf_path, warning = fetch_and_store_pdf(
        url=pdf_url,
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        fetch_binary=fetch_binary,
    )
    if local_pdf_path is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "failed to fetch PDF",
            "warnings": [],
            "errors": [warning] if warning else ["failed to fetch PDF"],
        }

    update_result = update_bib_entry(
        bib["path"],
        citekey,
        lambda entry, record: _entry_with_pdf_fields(
            entry,
            cast(NormalizedRecord, dict(record)),
            local_pdf_path=local_pdf_path,
            pdf_url=pdf_url,
        ),
    )
    if not update_result["found"]:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "citekey disappeared",
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": citekey,
        "local_pdf_path": local_pdf_path,
        "message": "fetched PDF",
        "warnings": [],
        "errors": [],
    }


def attach_pdf(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    source: str,
    fetch_binary=None,
) -> PdfAttachResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "could not resolve target bib",
            "warnings": [],
            "errors": resolved,
        }
    _config, bib = resolved

    read_result = read_bib_file(bib["path"])
    entries = read_result["entries"]
    index = _find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "citekey not found",
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    local_pdf_path, error = _store_pdf_source(
        source=source,
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        fetch_binary=fetch_binary,
    )
    if local_pdf_path is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "failed to attach PDF",
            "warnings": [],
            "errors": [error] if error else ["failed to attach PDF"],
        }

    update_result = update_bib_entry(
        bib["path"],
        citekey,
        lambda entry, record: _entry_with_pdf_fields(
            entry,
            cast(NormalizedRecord, dict(record)),
            local_pdf_path=local_pdf_path,
            pdf_url=source if source.startswith(("http://", "https://")) else None,
        ),
    )
    if not update_result["found"]:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "citekey disappeared",
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": citekey,
        "local_pdf_path": local_pdf_path,
        "source": source,
        "message": "attached PDF",
        "warnings": [],
        "errors": [],
    }


def attach_pdf_bytes(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    pdf_base64: str,
    source_url: str | None,
) -> PdfAttachBytesResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "could not resolve target bib",
            "warnings": [],
            "errors": resolved,
        }
    _config, bib = resolved

    try:
        data = base64.b64decode(pdf_base64, validate=True)
    except (ValueError, TypeError):
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "invalid PDF payload",
            "warnings": [],
            "errors": ["pdf_base64 must be valid base64"],
        }
    if not data.startswith(b"%PDF-"):
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "invalid PDF payload",
            "warnings": [],
            "errors": ["decoded payload is not a PDF"],
        }

    return _attach_pdf_data(
        bib_name=bib["name"],
        bib_path=bib["path"],
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        data=data,
        source_url=source_url,
    )


def _attach_pdf_data(
    *,
    bib_name: str,
    bib_path: str,
    papers_dir: str,
    citekey: str,
    data: bytes,
    source_url: str | None,
) -> PdfAttachBytesResult:
    read_result = read_bib_file(bib_path)
    entries = read_result["entries"]
    index = _find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "bib_name": bib_name,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "citekey not found",
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    destination = write_pdf_bytes(data=data, papers_dir=papers_dir, citekey=citekey)

    update_result = update_bib_entry(
        bib_path,
        citekey,
        lambda entry, record: _entry_with_pdf_fields(
            entry,
            cast(NormalizedRecord, dict(record)),
            local_pdf_path=destination,
            pdf_url=source_url,
        ),
    )
    if not update_result["found"]:
        return {
            "status": "error",
            "bib_name": bib_name,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "citekey disappeared",
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    return {
        "status": "ok",
        "bib_name": bib_name,
        "citekey": citekey,
        "local_pdf_path": destination,
        "source_url": source_url,
        "message": "attached PDF bytes",
        "warnings": [],
        "errors": [],
    }


def _store_pdf_source(
    *, source: str, papers_dir: str, citekey: str, fetch_binary=None
) -> tuple[str | None, str | None]:
    return store_pdf_source(
        source=source,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
    )


def _entry_with_pdf_fields(
    entry: BibtexEntry,
    record: NormalizedRecord,
    *,
    local_pdf_path: str,
    pdf_url: str | None,
) -> BibtexEntry:
    updated_record = dict(record)
    updated_record["local_pdf_path"] = local_pdf_path
    if pdf_url is not None:
        updated_record["pdf_url"] = pdf_url
    return record_to_bibtex_entry(
        cast(NormalizedRecord, updated_record),
        entry_type=entry["entry_type"],
    )


# ---------------------------------------------------------------------------
# PDF metadata extraction
# ---------------------------------------------------------------------------

PdfExtractionResult: TypeAlias = dict[str, Any]

_DOI_IN_TEXT_PATTERN = re.compile(
    r"(?i)\b(10\.\d{3,9}/[-._;()/:\w]+)\b"
)


def extract_pdf_metadata(path: str) -> PdfExtractionResult:
    """Extract DOI and title candidate from first pages of a PDF."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"doi": None, "title": None, "text_sample": None}

    file_path = Path(path)
    if not file_path.exists():
        return {"doi": None, "title": None, "text_sample": None}

    try:
        reader = PdfReader(str(file_path))
    except (OSError, ValueError):
        return {"doi": None, "title": None, "text_sample": None}

    text_pages: list[str] = []
    for page in reader.pages[:3]:
        try:
            text = page.extract_text()
            if text:
                text_pages.append(text)
        except (OSError, ValueError, AttributeError):
            continue

    full_text = "\n".join(text_pages)
    if not full_text.strip():
        return {"doi": None, "title": None, "text_sample": None}

    doi = _extract_doi_from_text(full_text)
    title = _extract_title_from_text(full_text)

    sample = full_text[:2000].strip() or None

    return {"doi": doi, "title": title, "text_sample": sample}


def _extract_doi_from_text(text: str) -> str | None:
    """Find first DOI in extracted text."""
    match = _DOI_IN_TEXT_PATTERN.search(text)
    if match is None:
        return None
    candidate = match.group(1).strip()
    candidate = re.sub(r"\s+", "", candidate)
    return candidate.lower()


def _extract_title_from_text(text: str) -> str | None:
    """Heuristic: first non-empty line that looks like a title."""
    skip_prefixes = (
        "doi:", "doi ", "http", "www.", "copyright", "©",
        "proceedings", "journal", "conference", "arxiv:",
        "received", "accepted", "published", "keywords:",
        "abstract", "introduction", "vol.", "pp.", "page",
        "fig.", "figure", "table", "issn", "isbn",
    )
    skip_patterns = (
        re.compile(r"^\s*\d+\s*$"),
        re.compile(r"^\s*[-–—]+\s*$"),
        re.compile(r"^\s*\*\s*$"),
    )

    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 10:
            continue
        lower = stripped.lower()
        if any(lower.startswith(p) for p in skip_prefixes):
            continue
        if any(p.match(stripped) for p in skip_patterns):
            continue
        if 10 <= len(stripped) <= 200:
            return stripped

    return None
