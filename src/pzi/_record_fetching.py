"""Pure metadata fetching pipeline for pzi add/capture workflow.

All functions here are either pure or have injectable I/O edges.
Separated from add_service.py to keep orchestration lean.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pzi.bibtex import NormalizedRecord
from pzi.metadata_sources import (
    fetch_crossref_record,
    fetch_openalex_record,
    fetch_semantic_scholar_record,
)
from pzi.flaresolverr import fetch_html_via_flaresolverr
from pzi.html_metadata import extract_metadata_from_html
from pzi.pdf_discovery import (
    DEFAULT_DISCOVERY_STEPS,
    PdfDiscoveryContext,
    apply_pdf_discovery,
)


def fetch_record_for_input(
    *,
    raw_value: str,
    classified: Mapping[str, object],
    server_url: str,
    fetch_web,
    fetch_search,
    unpaywall_email: str | None = None,
    s2_api_key: str | None = None,
    flaresolverr_url: str | None = None,
    fetch_unpaywall=None,
    fetch_crossref=None,
    fetch_openalex=None,
    fetch_s2=None,
    fetch_flaresolverr=None,
    pdf_url_candidates: list[str] | None = None,
    browser_pdf_cmd: str | None = None,
) -> NormalizedRecord:
    kind = classified["kind"]
    normalized = cast(str | None, classified["normalized"])
    fallback = _fallback_record_for_input(
        kind=cast(str, kind), normalized=normalized, raw_value=raw_value
    )

    def _discovery_context(
        translation_attachments: list[dict[str, object]] | None = None,
    ) -> PdfDiscoveryContext:
        return {
            "raw_value": raw_value,
            "server_url": server_url,
            "unpaywall_email": unpaywall_email,
            "s2_api_key": s2_api_key,
            "flaresolverr_url": flaresolverr_url,
            "browser_pdf_cmd": browser_pdf_cmd,
            "pdf_url_candidates": pdf_url_candidates,
            "fetch_web": fetch_web,
            "fetch_unpaywall": fetch_unpaywall,
            "fetch_crossref": fetch_crossref,
            "fetch_openalex": fetch_openalex,
            "fetch_s2": fetch_s2,
            "fetch_flaresolverr": fetch_flaresolverr,
            "translation_attachments": translation_attachments,
        }

    def _with_pdf_discovery(
        base_record: NormalizedRecord,
        *,
        translation_attachments: list[dict[str, object]] | None = None,
    ) -> NormalizedRecord:
        return apply_pdf_discovery(
            base_record,
            DEFAULT_DISCOVERY_STEPS,
            _discovery_context(translation_attachments=translation_attachments),
        )

    if kind == "doi" and normalized is not None:
        results = _safe_call(lambda: fetch_search(normalized, server_url=server_url))
        if results:
            best = dict(_merge_record_sources(results[0]["record"], fallback))
            return _with_pdf_discovery(
                best, translation_attachments=results[0].get("attachments")
            )

        meta = (fetch_crossref or fetch_crossref_record)(normalized)
        if meta is None:
            meta = (fetch_openalex or fetch_openalex_record)(normalized)
        if meta is None:
            s2_fn = fetch_s2 or (lambda d: fetch_semantic_scholar_record(d, api_key=s2_api_key))
            meta = s2_fn(normalized)
        if meta is not None:
            best = dict(_merge_record_sources(meta, fallback))
            return _with_pdf_discovery(best)

        from urllib.parse import urlsplit as _urlsplit

        raw_as_url = (
            raw_value if _urlsplit(raw_value).scheme in {"http", "https"} else None
        )
        if raw_as_url:
            web_results = _safe_call(
                lambda: fetch_web(raw_as_url, server_url=server_url)
            )
            if web_results:
                best = dict(
                    _merge_record_sources(web_results[0]["record"], fallback)
                )
                return _with_pdf_discovery(
                    best, translation_attachments=web_results[0].get("attachments")
                )

            if flaresolverr_url is not None:  # pragma: no branch
                fn = fetch_flaresolverr or (
                    lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
                )
                html = fn(raw_as_url)
                if html:  # pragma: no branch — covered by integration/browser tests
                    meta = extract_metadata_from_html(html)
                    if meta is not None:  # pragma: no branch — covered by integration/browser tests
                        best = dict(_merge_record_sources(meta, fallback))
                        return _with_pdf_discovery(best)

        suffix = (
            " (page may be Cloudflare-protected — configure flaresolverr_url to bypass)"
            if raw_as_url and flaresolverr_url is None
            else ""
        )
        raise ValueError(f"no metadata found for DOI: {normalized}{suffix}")

    if kind in {"url", "pdf_url"} and normalized is not None:
        results = _safe_call(lambda: fetch_web(normalized, server_url=server_url))
        if results:
            best = dict(results[0]["record"])
            best = _with_pdf_discovery(
                best, translation_attachments=results[0].get("attachments")
            )
            return _merge_record_sources(fallback, best)

        if flaresolverr_url is not None:
            fn = fetch_flaresolverr or (
                lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
            )
            html = fn(normalized)
            if html:
                meta = extract_metadata_from_html(html)
                if meta is not None:
                    best = dict(_merge_record_sources(meta, fallback))
                    return _with_pdf_discovery(best)

        raise ValueError(f"translation server returned no results for URL: {normalized}")

    return fallback


def safe_api_call(fn):
    """Run callable, swallowing urllib HTTPError and returning []."""
    import urllib.error

    try:
        return fn()
    except urllib.error.HTTPError:
        return []


def _fallback_record_for_input(
    *, kind: str, normalized: str | None, raw_value: str
) -> NormalizedRecord:
    if kind == "doi" and normalized is not None:
        return {"doi": normalized}
    if kind == "pdf_url" and normalized is not None:
        return {"pdf_url": normalized, "source_url": normalized}
    if kind == "url" and normalized is not None:
        return {
            "canonical_url": normalized,
            "source_url": normalized,
            "abstract_url": normalized,
        }
    return {"source_url": raw_value}


def merge_record_sources(
    base: Mapping[str, object], overrides: Mapping[str, object]
) -> NormalizedRecord:
    merged = dict(base)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return cast(NormalizedRecord, merged)


# Re-exports for test monkeypatching (imported back into add_service.py)
_safe_call = safe_api_call
_merge_record_sources = merge_record_sources
_fetch_record_for_input = fetch_record_for_input
