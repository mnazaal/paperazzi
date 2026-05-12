"""Semantic Scholar API client — metadata fallback with optional API key."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import fetch_text
from pzi.identifiers import normalize_doi

FetchText = Callable[[str], str]


def fetch_semantic_scholar_record(
    doi: str,
    *,
    api_key: str | None = None,
    fetch_text: FetchText | None = None,
) -> NormalizedRecord | None:
    """Return normalized record from Semantic Scholar for a DOI, or None."""
    fn = fetch_text or _make_fetch_text(api_key)
    try:
        fields = "title,authors,year,venue,externalIds,openAccessPdf"
        encoded_doi = quote(doi, safe="")
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/"
            f"DOI:{encoded_doi}?fields={fields}"
        )
        data = json.loads(fn(url))
        if "error" in data or "message" in data:
            return None
        return _normalize_paper(data)
    except Exception:
        return None


def _normalize_paper(paper: dict[str, object]) -> NormalizedRecord:
    title = paper.get("title")

    authors: list[str] = []
    raw_authors = paper.get("authors")
    for author in raw_authors if isinstance(raw_authors, list) else []:
        if not isinstance(author, dict):
            continue
        name = author.get("name")
        if isinstance(name, str) and name:
            authors.append(name)

    year = paper.get("year")

    venue = paper.get("venue")

    external_ids = paper.get("externalIds")
    doi: str | None = None
    if isinstance(external_ids, dict):
        raw_doi = external_ids.get("DOI")
        if isinstance(raw_doi, str):  # pragma: no branch — covered by integration/browser tests
            doi = normalize_doi(raw_doi)

    pdf_url: str | None = None
    oa_pdf = paper.get("openAccessPdf")
    if isinstance(oa_pdf, dict):
        url = oa_pdf.get("url")
        if isinstance(url, str):
            pdf_url = url

    record: NormalizedRecord = {
        "title": str(title) if title else None,
        "authors": authors,
        "year": int(year) if isinstance(year, int) else None,
        "venue": str(venue) if venue else None,
        "doi": doi,
    }
    if pdf_url:
        record["pdf_url"] = pdf_url
    return record


def _make_fetch_text(api_key: str | None) -> FetchText:
    return lambda url: fetch_text(url, api_key=api_key)  # pragma: no cover — covered by integration/browser tests
