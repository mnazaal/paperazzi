"""Pure HTTP API payload helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

MAX_PDF_URL_CANDIDATES = 20


def record_overrides_from_capture_body(body: dict[str, Any]) -> dict[str, object]:
    record_overrides: dict[str, object] = {}
    raw_tags = body.get("tags")
    if isinstance(raw_tags, list):
        record_overrides["tags"] = [
            tag for tag in raw_tags if isinstance(tag, str) and tag.strip()
        ]
    for body_key, record_key in [
        ("page_title", "fallback_title"),
        ("canonical_url", "fallback_canonical_url"),
        ("source_url", "fallback_source_url"),
        ("abstract_url", "fallback_abstract_url"),
        ("doi", "fallback_doi"),
    ]:
        value = body.get(body_key)
        if isinstance(value, str) and value.strip():
            record_overrides[record_key] = value.strip()
    # Rich embedded metadata from browser extension (Tier 2)
    _maybe_validate_authors_str(body, "embedded_authors", record_overrides, "fallback_authors")
    _maybe_set_fallback_str(body, "embedded_year", record_overrides, "fallback_year")
    _maybe_set_fallback_str(body, "embedded_venue", record_overrides, "fallback_venue")
    _maybe_set_fallback_str(body, "embedded_abstract", record_overrides, "fallback_abstract")
    _maybe_set_fallback_str(body, "embedded_volume", record_overrides, "fallback_volume")
    _maybe_set_fallback_str(body, "embedded_issue", record_overrides, "fallback_issue")
    _maybe_set_fallback_str(body, "embedded_pages", record_overrides, "fallback_pages")
    _maybe_set_fallback_str(body, "embedded_issn", record_overrides, "fallback_issn")
    _maybe_set_fallback_str(body, "embedded_isbn", record_overrides, "fallback_isbn")
    _maybe_set_fallback_str(body, "embedded_pdf_url", record_overrides, "fallback_pdf_url")
    # JSON-LD / OG fallbacks — used when citation_* meta is absent
    # Order: OG first, then JSON-LD (JSON-LD is more reliable, wins if both present)
    _maybe_set_fallback_str(body, "embedded_og_title", record_overrides, "fallback_title")
    _maybe_validate_authors_str(
        body, "embedded_jsonld_authors", record_overrides, "fallback_authors"
    )
    _maybe_set_fallback_str(body, "embedded_jsonld_title", record_overrides, "fallback_title")
    _maybe_set_fallback_str(body, "embedded_jsonld_year", record_overrides, "fallback_year")
    return record_overrides


def _maybe_set_fallback_str(
    body: dict[str, Any], body_key: str, overrides: dict[str, object], record_key: str
) -> None:
    """Set a fallback override from a string body field, if valid."""
    value = body.get(body_key)
    if isinstance(value, str) and value.strip():
        overrides[record_key] = value.strip()


def _maybe_validate_authors_str(
    body: dict[str, Any], body_key: str, overrides: dict[str, object], record_key: str
) -> None:
    """Convert author list to ' and '-separated string, if all entries are strings."""
    raw = body.get(body_key)
    if not isinstance(raw, list) or not raw:
        return
    valid: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            valid.append(item.strip())
        else:
            return  # reject mixed types — safety gate
    if not valid:
        return
    # Deduplicate preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for a in valid:
        if a not in seen:
            seen.add(a)
            deduped.append(a)
    overrides[record_key] = " and ".join(deduped)


def metadata_url_override_error(
    body: dict[str, Any],
    *,
    safe_url: Callable[[str], bool],
) -> str | None:
    for key in ("canonical_url", "source_url", "abstract_url"):
        value = body.get(key)
        if isinstance(value, str) and value.strip() and not safe_url(value):
            return f"{key} must be a public http(s) URL"
    return None


def pdf_url_candidates_from_body(
    body: dict[str, Any],
    *,
    safe_url: Callable[[str], bool],
    max_candidates: int = MAX_PDF_URL_CANDIDATES,
) -> list[str] | None | bool:
    raw_candidates = body.get("pdf_url_candidates")
    if not isinstance(raw_candidates, list):
        return None
    if len(raw_candidates) > max_candidates:
        return False
    candidates: list[str] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        stripped = candidate.strip()
        if not safe_url(stripped):
            return False
        candidates.append(stripped)
    return candidates


def capture_payload(
    result: dict[str, Any], *, include_diagnostics: bool = False
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
    }
    if include_diagnostics and result.get("metadata_diagnostics"):
        payload["metadata_diagnostics"] = result["metadata_diagnostics"]
    return payload


def search_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a search_bib result for the HTTP API."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "matches": result.get("matches", []),
        "total": len(result.get("matches", [])),
        "errors": result.get("errors", []),
    }


def entries_payload(result: dict[str, Any], offset: int, limit: int) -> dict[str, Any]:
    """Serialize a search_bib result as a paginated entries list."""
    matches = result.get("matches", [])
    total = len(matches)
    page = matches[offset : offset + limit]
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "entries": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "errors": result.get("errors", []),
    }


def detail_payload(record: dict[str, Any], bib_name: str | None) -> dict[str, Any]:
    """Serialize a single BibTeX record for the HTTP API."""
    tags = list(record.get("tags") or [])
    return {
        "status": "ok",
        "bib": bib_name,
        "citekey": record.get("citekey"),
        "entry": {
            "citekey": record.get("citekey"),
            "title": record.get("title"),
            "authors": list(record.get("authors") or []),
            "year": record.get("year"),
            "doi": record.get("doi"),
            "url": record.get("url"),
            "venue": record.get("venue"),
            "abstract": record.get("abstract"),
            "note": record.get("note"),
            "tags": sorted(tags),
            "local_pdf_path": record.get("local_pdf_path"),
            "pdf_url": record.get("pdf_url"),
        },
        "errors": [],
    }


def tag_list_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a list_tags result for the HTTP API."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "citekey": result.get("citekey"),
        "tags": result.get("tags", []),
        "errors": result.get("errors", []),
    }


def tag_change_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Serialize a tag add/remove result for the HTTP API."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "citekey": result.get("citekey"),
        "tags": result.get("tags", []),
        "changed": result.get("changed", False),
        "dry_run": result.get("dry_run", False),
        "message": result.get("message", ""),
        "errors": result.get("errors", []),
    }


def update_payload(
    result: dict[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Serialize an update_bib result for the HTTP API."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "dry_run": result.get("dry_run", True),
        "items": _items_payload(
            result.get("items", []), include_diagnostics=include_diagnostics
        ),
        "errors": result.get("errors", []),
    }


def promote_payload(
    result: dict[str, Any], *, include_diagnostics: bool = False
) -> dict[str, Any]:
    """Serialize a promote_bib result for the HTTP API."""
    return {
        "status": result["status"],
        "bib": result.get("bib_name"),
        "dry_run": result.get("dry_run", True),
        "keep_preprint": result.get("keep_preprint", True),
        "items": _items_payload(
            result.get("items", []), include_diagnostics=include_diagnostics
        ),
        "summary": result.get("summary", {}),
        "errors": result.get("errors", []),
    }


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
