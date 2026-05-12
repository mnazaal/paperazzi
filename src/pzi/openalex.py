"""OpenAlex API client — free metadata fallback, no auth required."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import fetch_text as _fetch_text
from pzi.identifiers import normalize_doi

FetchText = Callable[[str], str]


def fetch_openalex_record(
    doi: str, *, fetch_text: FetchText | None = None
) -> NormalizedRecord | None:
    """Return normalized record from OpenAlex for a DOI, or None on failure."""
    fn = fetch_text or _fetch_text
    try:
        url = f"https://api.openalex.org/works/doi:{quote(doi, safe='')}"
        data = json.loads(fn(url))
        if "id" not in data:
            return None
        return _normalize_work(data)
    except Exception:
        return None


def _normalize_work(work: dict[str, object]) -> NormalizedRecord:
    title = work.get("title")

    authors: list[str] = []
    raw_authorships = work.get("authorships")
    for authorship in raw_authorships if isinstance(raw_authorships, list) else []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict):  # pragma: no branch — covered by integration/browser tests
            name = author.get("display_name")
            if isinstance(name, str) and name:
                authors.append(name)

    year = work.get("publication_year")

    venue: str | None = None
    primary_location = work.get("primary_location")
    if isinstance(primary_location, dict):
        source = primary_location.get("source")
        if isinstance(source, dict):
            venue = source.get("display_name")  # type: ignore[assignment]

    raw_doi = work.get("doi")
    doi: str | None = None
    if isinstance(raw_doi, str):  # pragma: no branch — covered by integration/browser tests
        # OpenAlex returns full URL like https://doi.org/10.xxxx/yyyy
        doi = normalize_doi(raw_doi.replace("https://doi.org/", ""))

    pdf_url: str | None = None
    oa = work.get("open_access")
    if isinstance(oa, dict):
        oa_url = oa.get("oa_url")
        if isinstance(oa_url, str):  # pragma: no branch — covered by integration/browser tests
            pdf_url = oa_url

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


