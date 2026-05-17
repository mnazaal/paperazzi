"""Consolidated metadata API clients for fallback beyond Zotero translation-server.

Sources: Crossref, OpenAlex, Semantic Scholar, DOAJ, Europe PMC.
All follow the same pattern: fetch_text() → JSON → normalize.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import fetch_text as _fetch_text
from pzi.identifiers import normalize_doi

FetchText = Callable[[str], str]


# ============================================================================
# Crossref
# ============================================================================


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
        return _crossref_normalize_work(work)
    except (OSError, json.JSONDecodeError, ValueError):
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
        return _crossref_extract_pdf_url(work)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _crossref_normalize_work(work: dict[str, object]) -> NormalizedRecord:
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

    pdf_url = _crossref_extract_pdf_url(work)
    if pdf_url:
        record["pdf_url"] = pdf_url

    return record


def _crossref_extract_pdf_url(work: dict[str, object]) -> str | None:
    """Extract PDF URL from Crossref work link[] field."""
    links = work.get("link")
    if not isinstance(links, list):
        return None

    for link in links:
        if not isinstance(link, dict):
            continue
        content_type = link.get("content-type", "")
        if isinstance(content_type, str) and "application/pdf" in content_type.lower():
            url = link.get("URL")
            if isinstance(url, str) and url.strip():
                return url.strip()

    for link in links:
        if not isinstance(link, dict):
            continue
        url = link.get("URL", "")
        if isinstance(url, str) and url.strip():
            url_lower = url.lower()
            if url_lower.endswith(".pdf") or "/pdf/" in url_lower:
                return url.strip()

    return None


# ============================================================================
# OpenAlex
# ============================================================================


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
        return _openalex_normalize_work(data)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _openalex_normalize_work(work: dict[str, object]) -> NormalizedRecord:
    title = work.get("title")

    authors: list[str] = []
    raw_authorships = work.get("authorships")
    for authorship in raw_authorships if isinstance(raw_authorships, list) else []:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict):
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
    if isinstance(raw_doi, str):
        doi = normalize_doi(raw_doi.replace("https://doi.org/", ""))

    pdf_url: str | None = None
    oa = work.get("open_access")
    if isinstance(oa, dict):
        oa_url = oa.get("oa_url")
        if isinstance(oa_url, str):
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


# ============================================================================
# Semantic Scholar
# ============================================================================


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
        return _s2_normalize_paper(data)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _s2_normalize_paper(paper: dict[str, object]) -> NormalizedRecord:
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
        if isinstance(raw_doi, str):
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
    return lambda url: _fetch_text(url, api_key=api_key)


# ============================================================================
# DOAJ
# ============================================================================


def fetch_doaj_pdf_url(
    doi: str, *, fetch_text: FetchText | None = None
) -> str | None:
    """Return a PDF URL from DOAJ for a DOI, or None."""
    fn = fetch_text or _fetch_text
    try:
        encoded_doi = quote(doi, safe="")
        url = f"https://doaj.org/api/search/articles/doi:{encoded_doi}"
        data = json.loads(fn(url))
        results = data.get("results", [])
        if not results:
            return None

        article = results[0]
        return _doaj_extract_pdf_url(article)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _doaj_extract_pdf_url(article: dict[str, object]) -> str | None:
    """Extract PDF URL from DOAJ article bibjson.link[] field."""
    bibjson = article.get("bibjson")
    if not isinstance(bibjson, dict):
        return None

    links = bibjson.get("link", [])
    if not isinstance(links, list):
        return None

    for link in links:
        if not isinstance(link, dict):
            continue
        content_type = link.get("content_type", "")
        url = link.get("url", "")

        if (
            isinstance(content_type, str)
            and content_type.upper() == "PDF"
            and isinstance(url, str)
            and url.strip()
        ):
            return url.strip()

    for link in links:
        if not isinstance(link, dict):
            continue
        url = link.get("url", "")
        if isinstance(url, str) and url.strip().lower().endswith(".pdf"):
            return url.strip()

    return None


# ============================================================================
# Europe PMC
# ============================================================================


def fetch_europepmc_pdf_url(
    doi: str, *, fetch_text: FetchText | None = None
) -> str | None:
    """Return an open-access PDF URL from Europe PMC, or None."""
    fn = fetch_text or _fetch_text
    try:
        encoded_doi = quote(doi, safe="")
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query=DOI:{encoded_doi}&resultType=core&format=json"
        )
        data = json.loads(fn(url))
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None

        result = results[0]
        return _epmc_extract_pdf_url(result)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _epmc_extract_pdf_url(result: dict[str, object]) -> str | None:
    """Extract OA PDF URL from Europe PMC result fullTextUrlList."""
    full_text_url_list = result.get("fullTextUrlList")
    if not isinstance(full_text_url_list, dict):
        return None

    urls = full_text_url_list.get("fullTextUrl", [])
    if not isinstance(urls, list):
        return None

    for url_obj in urls:
        if not isinstance(url_obj, dict):
            continue
        doc_style = url_obj.get("documentStyle", "")
        availability = url_obj.get("availability", "")
        pdf_url = url_obj.get("url", "")

        if (
            isinstance(doc_style, str)
            and doc_style.lower() == "pdf"
            and isinstance(availability, str)
            and availability.lower() in ("openaccess", "open access")
            and isinstance(pdf_url, str)
            and pdf_url.strip()
        ):
            return pdf_url.strip()

    for url_obj in urls:
        if not isinstance(url_obj, dict):
            continue
        doc_style = url_obj.get("documentStyle", "")
        pdf_url = url_obj.get("url", "")
        if (
            isinstance(doc_style, str)
            and doc_style.lower() == "pdf"
            and isinstance(pdf_url, str)
            and pdf_url.strip()
        ):
            return pdf_url.strip()

    return None


# Aliases for test imports
# ------------------------
_extract_pdf_url = _epmc_extract_pdf_url
_doaj_extract_pdf_url_alias = _doaj_extract_pdf_url
_normalize_work = _openalex_normalize_work

_crossref_normalize_work = _crossref_normalize_work
_crossref_extract_pdf_url = _crossref_extract_pdf_url
_openalex_normalize_work = _openalex_normalize_work
_doaj_extract_pdf_url = _doaj_extract_pdf_url
_epmc_extract_pdf_url = _epmc_extract_pdf_url
_s2_normalize_paper = _s2_normalize_paper
