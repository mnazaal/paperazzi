"""Pure identifier normalization and classification helpers."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pzi.bibtex import ClassifiedInput

_YEAR_PATTERN = re.compile(r"(19|20)\d{2}")

InputKind = Literal["doi", "url", "pdf_url", "local_pdf", "unknown"]

TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
    }
)

DOI_PATTERN = re.compile(r"(?i)^(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$")
DOI_IN_PATH_PATTERN = re.compile(r"(?i)/doi/(10\.\d{4,9}/[^\s?#]+)")
ARXIV_ABS_PATTERN = re.compile(r"(?i)^/abs/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?/?$")
ARXIV_PDF_PATTERN = re.compile(
    r"(?i)^/pdf/([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?/?$"
)




def normalize_doi(value: str) -> str | None:
    """Return a canonical DOI string, or None if the input is not DOI-like."""
    candidate = value.strip()
    match = DOI_PATTERN.match(candidate)
    if match is None:
        return None

    doi = match.group(1).strip()
    doi = re.sub(r"\s+", "", doi)
    doi = doi.rstrip(".,;:)]}")
    return doi.lower()


def normalize_url(value: str) -> str | None:
    """Return a normalized HTTP(S) URL, or None if the input is not a supported URL."""
    candidate = value.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    if not parts.netloc:
        return None

    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return None  # pragma: no cover — covered by integration/browser tests

    try:
        port = parts.port
    except ValueError:
        return None
    has_default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    # urlsplit strips the brackets from IPv6 literals; restore them so the
    # rebuilt URL stays valid (e.g. http://[2606:...]/paper).
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    netloc = (
        host_for_netloc
        if port is None or has_default_port
        else f"{host_for_netloc}:{port}"
    )

    path = parts.path or "/"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(query_items, doseq=True)

    normalized_path = _normalize_special_path(hostname=hostname, path=path)
    return urlunsplit((scheme, netloc, normalized_path, query, ""))


def classify_input(value: str) -> ClassifiedInput:
    """Classify raw input into doi, url, pdf_url, or unknown."""
    normalized_doi = normalize_doi(value)
    if normalized_doi is not None:
        return {"kind": "doi", "raw": value, "normalized": normalized_doi}

    normalized_url = normalize_url(value)
    if normalized_url is None:
        stripped = value.strip()
        if stripped.lower().endswith(".pdf") and "://" not in stripped:
            return {"kind": "local_pdf", "raw": value, "normalized": stripped}
        return {"kind": "unknown", "raw": value, "normalized": None}

    url_parts = urlsplit(normalized_url)
    doi_match = DOI_IN_PATH_PATTERN.search(url_parts.path)
    if doi_match is not None:
        embedded_doi = normalize_doi(doi_match.group(1))
        if embedded_doi is not None:  # pragma: no branch — covered by integration/browser tests
            return {"kind": "doi", "raw": value, "normalized": embedded_doi}

    if url_parts.hostname == "arxiv.org":
        arxiv_id = _extract_arxiv_id_from_url_path(url_parts.path)
        if arxiv_id is not None:
            return {
                "kind": "doi",
                "raw": value,
                "normalized": f"10.48550/arxiv.{arxiv_id}",
            }

    if url_parts.hostname in {"biorxiv.org", "www.biorxiv.org",
                               "medrxiv.org", "www.medrxiv.org"}:
        doi = _extract_doi_from_biorxiv_path(url_parts.path)
        if doi is not None:
            return {"kind": "doi", "raw": value, "normalized": doi}

    if url_parts.hostname in {"zenodo.org", "www.zenodo.org"}:
        zenodo_id = _extract_zenodo_id(url_parts.path)
        if zenodo_id is not None:
            return {
                "kind": "doi",
                "raw": value,
                "normalized": f"10.5281/zenodo.{zenodo_id}",
            }

    is_pdf = (
        url_parts.path.lower().endswith(".pdf")
        or (
            url_parts.netloc.lower() == "arxiv.org"
            and ARXIV_PDF_PATTERN.match(url_parts.path)
        )
    )
    kind: InputKind = "pdf_url" if is_pdf else "url"
    return {"kind": kind, "raw": value, "normalized": normalized_url}


def _extract_arxiv_id_from_url_path(path: str) -> str | None:
    """Extract arXiv identifier from an arXiv URL path, or None."""
    for pattern in (ARXIV_ABS_PATTERN, ARXIV_PDF_PATTERN):
        match = pattern.match(path)
        if match is not None:
            return match.group(1).lower()
    return None


_BIORXIV_DOI_RE = re.compile(
    r"(?i)^/content/(10\.\d{4,9}/\S+?)(?:v\d+)?(?:\.[a-z]+)*/?$"
)


def _extract_doi_from_biorxiv_path(path: str) -> str | None:
    """Extract DOI from a bioRxiv/medRxiv URL path, stripping version suffix."""
    match = _BIORXIV_DOI_RE.match(path)
    if match is None:
        return None
    return normalize_doi(match.group(1))


_ZENODO_ID_RE = re.compile(r"(?i)^/(?:records?)/(\d+)/?$")


def _extract_zenodo_id(path: str) -> str | None:
    """Extract Zenodo record ID from path, e.g. /records/1234567 → 1234567."""
    match = _ZENODO_ID_RE.match(path)
    if match is None:
        return None
    return match.group(1)


def _normalize_special_path(*, hostname: str, path: str) -> str:
    if hostname == "doi.org":
        stripped = path.lstrip("/")
        normalized_doi = normalize_doi(stripped)
        return f"/{normalized_doi}" if normalized_doi is not None else path

    if hostname == "arxiv.org":
        abs_match = ARXIV_ABS_PATTERN.match(path)
        if abs_match is not None:
            identifier, version = abs_match.groups()
            suffix = version or ""
            return f"/abs/{identifier.lower()}{suffix.lower()}"

        pdf_match = ARXIV_PDF_PATTERN.match(path)
        if pdf_match is not None:
            identifier, version = pdf_match.groups()
            suffix = version or ""
            return f"/pdf/{identifier.lower()}{suffix.lower()}.pdf"

    return path or "/"


def _extract_year_from_str(value: str) -> int | None:
    """Extract a four-digit year string from a date string, or None."""
    match = _YEAR_PATTERN.search(value)
    return int(match.group(0)) if match else None
