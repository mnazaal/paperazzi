"""DOAJ API client — OA PDF discovery for open access journals."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from pzi.fetch_helpers import fetch_text as _fetch_text

FetchText = Callable[[str], str]


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
        return _extract_pdf_url(article)
    except Exception:
        return None


def _extract_pdf_url(article: dict[str, object]) -> str | None:
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

    # Fallback: accept any link that looks like a PDF
    for link in links:
        if not isinstance(link, dict):
            continue
        url = link.get("url", "")
        if isinstance(url, str) and url.strip().lower().endswith(".pdf"):
            return url.strip()

    return None
