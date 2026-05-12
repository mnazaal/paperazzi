"""PDF retry workflow service."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi.bib_repository import read_bib_file, update_bib_entry
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    extract_note_field,
    record_to_bibtex_entry,
)
from pzi.pdf import fetch_and_store_pdf
from pzi.service_common import _find_entry_index, load_and_resolve_bib

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

    destination = Path(papers_dir) / f"{citekey}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)

    update_result = update_bib_entry(
        bib_path,
        citekey,
        lambda entry, record: _entry_with_pdf_fields(
            entry,
            cast(NormalizedRecord, dict(record)),
            local_pdf_path=str(destination),
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
        "local_pdf_path": str(destination),
        "source_url": source_url,
        "message": "attached PDF bytes",
        "warnings": [],
        "errors": [],
    }


def _store_pdf_source(
    *, source: str, papers_dir: str, citekey: str, fetch_binary=None
) -> tuple[str | None, str | None]:
    if source.startswith(("http://", "https://")):
        return fetch_and_store_pdf(
            url=source,
            papers_dir=papers_dir,
            citekey=citekey,
            fetch_binary=fetch_binary,
        )

    source_path = Path(source).expanduser()
    if not source_path.exists():
        return None, f"PDF source not found: {source}"
    try:
        data = source_path.read_bytes()
    except OSError as exc:
        return None, f"failed to read PDF source {source}: {exc}"
    if not data.startswith(b"%PDF-"):
        return None, f"source is not a PDF: {source}"

    destination = Path(papers_dir) / f"{citekey}.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return str(destination), None


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
