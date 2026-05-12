"""Crossref API client — metadata fallback when Zotero translation fails."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import fetch_text as _fetch_text
from pzi.identifiers import normalize_doi

FetchText = Callable[[str], str]


def fetch_crossref_record(
    doi: str, *, fetch_text: FetchText | None = None
) -> NormalizedRecord | None:
    """Return normalized record from Crossref for a DOI, or None on failure."""
    fn = fetch_text or _fetch_text
    try:
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        data = json.loads(fn(url))
        work = data.get("message")
        if not isinstance(work, dict):
            return None
        return _normalize_work(work)
    except Exception:
        return None


def fetch_crossref_pdf_url(
    doi: str, *, fetch_text: FetchText | None = None
) -> str | None:
    """Return a direct PDF URL from Crossref link[] field, or None."""
    fn = fetch_text or _fetch_text
    try:
        url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
        data = json.loads(fn(url))
        work = data.get("message")
        if not isinstance(work, dict):
            return None
        return _extract_pdf_url(work)
    except Exception:
        return None


def _normalize_work(work: dict[str, object]) -> NormalizedRecord:
    title_list = work.get("title")
    title = title_list[0] if isinstance(title_list, list) and title_list else None

    authors: list[str] = []
    raw_authors = work.get("author")
    for author in raw_authors if isinstance(raw_authors, list) else []:
        if not isinstance(author, dict):
            continue
        given = author.get("given") or ""
        family = author.get("family") or ""
        if family and given:
            authors.append(f"{family}, {given}")
        elif family:
            authors.append(str(family))

    year: int | None = None
    for date_field in ("published-print", "published-online", "created"):
        raw_date = work.get(date_field)
        date_parts = raw_date.get("date-parts") if isinstance(raw_date, dict) else None
        if (
            isinstance(date_parts, list)
            and date_parts
            and isinstance(date_parts[0], list)
            and date_parts[0]
        ):
            candidate = date_parts[0][0]
            if isinstance(candidate, int):
                year = candidate
                break

    container = work.get("container-title")
    venue = container[0] if isinstance(container, list) and container else None

    raw_doi = work.get("DOI")
    doi = normalize_doi(str(raw_doi)) if raw_doi else None

    record: NormalizedRecord = {
        "title": str(title) if title else None,
        "authors": authors,
        "year": year,
        "venue": str(venue) if venue else None,
        "doi": doi,
    }

    pdf_url = _extract_pdf_url(work)
    if pdf_url:
        record["pdf_url"] = pdf_url

    return record


def _extract_pdf_url(work: dict[str, object]) -> str | None:
    """Extract PDF URL from Crossref work link[] field."""
    links = work.get("link")
    if not isinstance(links, list):
        return None

    # First pass: look for explicit PDF content-type
    for link in links:
        if not isinstance(link, dict):
            continue
        content_type = link.get("content-type", "")
        if isinstance(content_type, str) and "application/pdf" in content_type.lower():
            url = link.get("URL")
            if isinstance(url, str) and url.strip():
                return url.strip()

    # Second pass: look for PDF URL patterns (some publishers don't set content-type)
    for link in links:
        if not isinstance(link, dict):
            continue
        url = link.get("URL", "")
        if isinstance(url, str) and url.strip():
            url_lower = url.lower()
            if url_lower.endswith(".pdf") or "/pdf/" in url_lower:
                return url.strip()

    return None
