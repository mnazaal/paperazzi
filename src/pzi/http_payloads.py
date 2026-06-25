"""HTTP API response payload builders.

Pure functions that shape service results into HTTP response bodies.
No I/O, no side effects, no imports from CLI/HTTP handler machinery.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pzi.bibtex import normalize_authors


def capture_payload(
    result: Mapping[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    payload = {
        "status": result["status"],
        "bib": result["bib_name"],
        "citekey": result["citekey"],
        "action": result["action"],
        "pdf_path": result["pdf_path"],
        "pdf_url": result.get("pdf_url"),
        "pdf_status": result.get(
            "pdf_status",
            "direct_saved" if result.get("pdf_path") else "none",
        ),
        "pdf_error": result.get("pdf_error"),
        "pdf_suggestion": result.get("pdf_suggestion"),
        "dry_run": result["dry_run"],
        "message": result["message"],
        "warnings": result["warnings"],
        "errors": result["errors"],
        "changed_fields": result.get("changed_fields", []),
    }
    if include_diagnostics and result.get("metadata_diagnostics"):
        payload["metadata_diagnostics"] = result["metadata_diagnostics"]
    if result.get("pdf_request"):
        payload["pdf_request"] = result["pdf_request"]
    return payload


def _base_payload(result: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    """Common payload skeleton: status, bib, errors."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "errors": result.get("errors", []),
        **extra,
    }


def search_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize a search_bib result for the HTTP API."""
    matches = result.get("matches", [])
    return _base_payload(result, matches=matches, total=len(matches))


def entries_payload(result: Mapping[str, Any], offset: int, limit: int) -> dict[str, Any]:
    """Serialize entries from list_entries or legacy search_bib results."""
    if "items" in result:
        extra = {
            "entries": result.get("items", []),
            "total": result.get("total", 0),
            "offset": result.get("offset", offset),
            "limit": result.get("limit", limit),
        }
        if "sort" in result:
            extra["sort"] = result["sort"]
        return _base_payload(result, **extra)

    matches = result.get("matches", [])
    return _base_payload(
        result,
        entries=matches[offset : offset + limit],
        total=len(matches),
        offset=offset,
        limit=limit,
    )


def detail_payload(record: Mapping[str, Any], bib_name: str | None) -> dict[str, Any]:
    """Serialize a single BibTeX record for the HTTP API."""
    tags = list(record.get("tags") or [])
    return {
        "status": "ok",
        "bib": bib_name,
        "citekey": record.get("citekey"),
        "entry": {
            "citekey": record.get("citekey"),
            "title": record.get("title"),
            "authors": normalize_authors(record.get("authors")),
            "year": record.get("year"),
            "doi": record.get("doi"),
            "url": record.get("canonical_url") or record.get("source_url"),
            "venue": record.get("venue"),
            "abstract": record.get("abstract"),
            "note": record.get("note"),
            "tags": sorted(tags),
            "local_pdf_path": record.get("local_pdf_path"),
            "pdf_url": record.get("pdf_url"),
        },
        "errors": [],
    }


def tag_list_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize a list_tags result for the HTTP API."""
    return _base_payload(
        result,
        citekey=result.get("citekey"),
        tags=result.get("tags", []),
    )


def tag_change_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize a tag add/remove result for the HTTP API."""
    return _base_payload(
        result,
        citekey=result.get("citekey"),
        tags=result.get("tags", []),
        changed=result.get("changed", False),
        dry_run=result.get("dry_run", False),
        message=result.get("message", ""),
    )


def update_payload(
    result: Mapping[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Serialize an update_bib result for the HTTP API."""
    return _base_payload(
        result,
        dry_run=result.get("dry_run", True),
        items=_items_payload(
            result.get("items", []), include_diagnostics=include_diagnostics
        ),
    )


def promote_payload(
    result: Mapping[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Serialize a promote_bib result for the HTTP API."""
    return _base_payload(
        result,
        dry_run=result.get("dry_run", True),
        keep_preprint=result.get("keep_preprint", True),
        items=_items_payload(
            result.get("items", []), include_diagnostics=include_diagnostics
        ),
        summary=result.get("summary", {}),
    )


def _items_payload(items: object, *, include_diagnostics: bool) -> list[Any]:
    if not isinstance(items, list):
        return []
    if include_diagnostics:
        return items
    filtered: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            filtered.append(item)
            continue
        clean = dict(item)
        clean.pop("metadata_diagnostics", None)
        filtered.append(clean)
    return filtered
