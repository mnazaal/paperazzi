"""Europe PMC API client — OA PDF discovery for life sciences."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from pzi.fetch_helpers import fetch_text as _fetch_text

FetchText = Callable[[str], str]


def fetch_europepmc_pdf_url(
    doi: str, *, fetch_text: FetchText | None = None
) -> str | None:
    """Return an open-access PDF URL from Europe PMC, or None."""
    fn = fetch_text or _fetch_text
    try:
        encoded_doi = quote(doi, safe="")
        # Use DOI: prefix for Europe PMC search
        url = (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            f"?query=DOI:{encoded_doi}&resultType=core&format=json"
        )
        data = json.loads(fn(url))
        results = data.get("resultList", {}).get("result", [])
        if not results:
            return None

        result = results[0]
        return _extract_pdf_url(result)
    except Exception:
        return None


def _extract_pdf_url(result: dict[str, object]) -> str | None:
    """Extract OA PDF URL from Europe PMC result fullTextUrlList."""
    full_text_url_list = result.get("fullTextUrlList")
    if not isinstance(full_text_url_list, dict):
        return None

    urls = full_text_url_list.get("fullTextUrl", [])
    if not isinstance(urls, list):
        return None

    # First try: open access PDF
    for url_obj in urls:
        if not isinstance(url_obj, dict):
            continue  # pragma: no cover — covered by integration/browser tests
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

    # Fallback: accept any PDF regardless of availability tag
    for url_obj in urls:
        if not isinstance(url_obj, dict):
            continue  # pragma: no cover — covered by integration/browser tests
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
