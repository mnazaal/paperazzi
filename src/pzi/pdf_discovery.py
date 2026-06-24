"""Pure, composable PDF URL discovery pipeline.

Each step is a pure function  (record, context) -> record.
Steps are composed into a fallback chain that runs until pdf_url is found.

Also includes PDF candidate extraction helpers.
"""

from __future__ import annotations

import re as _re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit, urlunsplit

from pzi.bibtex import NormalizedRecord
from pzi.url_safety import safe_public_http_url

PdfDiscoveryContext: TypeAlias = dict[str, Any]
DNS_LOOKUP_TIMEOUT_SECONDS = 0.25

PdfCandidate: TypeAlias = dict[str, Any]

PdfDiscoveryStep = Callable[[NormalizedRecord, PdfDiscoveryContext], NormalizedRecord]


# ---------------------------------------------------------------------------
# PDF candidate extraction
# ---------------------------------------------------------------------------


def landing_page_urls(
    *, base_record: Mapping[str, object], raw_value: str
) -> list[str]:
    candidates: list[str] = []
    for value in [
        base_record.get("canonical_url"),
        base_record.get("source_url"),
        base_record.get("abstract_url"),
        raw_value,
    ]:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            continue
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def pdf_candidates_from_record(
    *, base_record: Mapping[str, object], raw_value: str
) -> list[PdfCandidate]:
    pdf_url = base_record.get("pdf_url")
    if isinstance(pdf_url, str) and pdf_url.strip():
        return [{"source": "record", "url": pdf_url.strip()}]

    return [
        {"source": "landing_page", "url": url}
        for url in landing_page_urls(base_record=base_record, raw_value=raw_value)
    ]


# ---------------------------------------------------------------------------
# Discovery pipeline
# ---------------------------------------------------------------------------


def apply_pdf_discovery(
    record: NormalizedRecord,
    steps: list[PdfDiscoveryStep],
    context: PdfDiscoveryContext,
) -> NormalizedRecord:
    """Run PDF discovery steps in order until pdf_url is populated."""
    for step in steps:
        if record.get("pdf_url"):
            break
        record = step(record, context)
    return record


def apply_pdf_discovery_parallel(
    record: NormalizedRecord,
    steps: list[PdfDiscoveryStep],
    context: PdfDiscoveryContext,
    *,
    max_workers: int = 4,
) -> NormalizedRecord:
    """Run PDF discovery with HTTP steps (web_attachment, doi_pdf, unpaywall)
    executed in parallel. Pure steps run sequentially first, browser step
    runs last as fallback.

    ``max_workers`` controls the thread pool size for parallel HTTP steps.
    """
    # Phase 1: run pure/fast steps sequentially
    pure_step_names = {
        "arxiv_step", "preprint_pdf_step", "translation_attachment_step",
        "pdf_url_candidates_step",
    }
    for step in steps:
        if record.get("pdf_url"):
            return record
        if step.__name__ in pure_step_names:
            record = step(record, context)

    if record.get("pdf_url"):
        return record

    # Phase 2: run HTTP steps in parallel
    http_steps = [
        step for step in steps
        if step.__name__ not in pure_step_names and step.__name__ != "browser_pdf_step"
    ]
    if http_steps:
        from concurrent.futures import ThreadPoolExecutor

        # Run all HTTP steps concurrently, but select the winner by the step's
        # position in the fallback chain (its source priority), not by whichever
        # network call returns first.  This keeps parallel mode's source ranking
        # identical to the sequential path.
        with ThreadPoolExecutor(max_workers=min(max_workers, len(http_steps))) as pool:
            futures = {step: pool.submit(step, record, context) for step in http_steps}
            results: dict[PdfDiscoveryStep, NormalizedRecord | None] = {}
            for step, future in futures.items():
                try:
                    results[step] = future.result()
                except Exception:
                    results[step] = None
        for step in http_steps:
            result = results.get(step)
            if result is not None and result.get("pdf_url"):
                return result

    # Phase 3: browser fallback
    for step in steps:
        if record.get("pdf_url"):
            return record
        if step.__name__ == "browser_pdf_step":
            record = step(record, context)

    return record


def translation_attachment_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Use PDF URL from translation-server attachment list."""
    attachments = context.get("translation_attachments")
    if not attachments:
        return record

    for attachment in attachments:
        if not isinstance(attachment, Mapping):
            continue

        url = attachment.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        normalized = url.strip()
        if not _safe_public_http_url(normalized):
            continue

        updated = dict(record)
        updated["pdf_url"] = normalized
        updated["pdf_source"] = "translation_attachment"
        return cast(NormalizedRecord, updated)

    return record


def pdf_url_candidates_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Use first non-empty candidate from externally-supplied list."""
    candidates = context.get("pdf_url_candidates")
    if not candidates:
        return record

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            normalized = candidate.strip()
            if not _safe_public_http_url(normalized) and not _existing_pdf_path(normalized):
                continue
            updated = dict(record)
            updated["pdf_url"] = normalized
            updated["pdf_source"] = "pdf_url_candidates"
            return cast(NormalizedRecord, updated)

    return record


def _safe_public_http_url(value: str, *, dns_timeout: float = DNS_LOOKUP_TIMEOUT_SECONDS) -> bool:
    return safe_public_http_url(value, dns_timeout=dns_timeout)


def _existing_pdf_path(value: str) -> bool:
    path = Path(value).expanduser()
    return path.is_file() and path.suffix.lower() == ".pdf"


def web_attachment_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Fetch landing pages via translation-server /web and use first PDF attachment.

    Also backfills canonical_url / source_url / abstract_url if the translator
    result provides them and the record currently lacks them.
    """

    fetch_web = context["fetch_web"]
    candidate_urls = landing_page_urls(base_record=record, raw_value=context["raw_value"])

    for url in candidate_urls:
        try:
            cookies = context.get("cookies")
            if isinstance(cookies, str) and cookies.strip():
                results = fetch_web(url, server_url=context["server_url"], cookies=cookies)
            else:
                results = fetch_web(url, server_url=context["server_url"])
        except (OSError, ValueError):
            continue

        for result in results:
            attachments = result.get("attachments")
            if not isinstance(attachments, list) or not attachments:
                continue

            for attachment in attachments:
                if not isinstance(attachment, Mapping):
                    continue

                pdf_url = attachment.get("url")
                if not isinstance(pdf_url, str) or not pdf_url.strip():
                    continue
                normalized_pdf_url = pdf_url.strip()
                if not _safe_public_http_url(normalized_pdf_url):
                    continue

                updated = dict(record)
                updated["pdf_url"] = normalized_pdf_url
                updated["pdf_source"] = "web_attachment"

                result_record = result.get("record")
                if isinstance(result_record, Mapping):  # pragma: no branch
                    for key in ("canonical_url", "source_url", "abstract_url"):
                        value = result_record.get(key)
                        if (
                            isinstance(value, str)
                            and value.strip()
                            and not record.get(key)
                        ):
                            updated[key] = value

                return cast(NormalizedRecord, updated)

    return record


def browser_pdf_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Discover PDF URL using external browser hook command or server API."""
    api_url = context.get("api_url")
    browser_pdf_cmd = context.get("browser_pdf_cmd")
    if api_url is None and browser_pdf_cmd is None:
        return record

    doi = record.get("doi") if isinstance(record.get("doi"), str) else None

    for url in landing_page_urls(base_record=record, raw_value=context["raw_value"]):
        pdf_url: str | None = None

        # Prefer server-side persistent browser when available.
        if api_url is not None:
            from pzi.server_browser import discover_via_server_api
            pdf_url = discover_via_server_api(
                api_url, url, doi=doi,
                auth_token=context.get("api_auth_token"),
            )

        # Fall back to subprocess browser hook.
        if pdf_url is None and browser_pdf_cmd is not None:
            from pzi.browser_pdf import discover_pdf_url_with_browser
            pdf_url = discover_pdf_url_with_browser(
                command=browser_pdf_cmd,
                page_url=url,
                doi=doi,
            )

        if pdf_url and _safe_public_http_url(pdf_url):
            updated = dict(record)
            updated["pdf_url"] = pdf_url
            updated["pdf_source"] = "browser_pdf"
            return cast(NormalizedRecord, updated)

    return record


def doi_pdf_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Resolve PDF via Crossref, Europe PMC, and DOAJ."""
    doi = record.get("doi")
    if not isinstance(doi, str) or not doi.strip():
        return record

    from pzi.metadata_sources import (
        fetch_crossref_pdf_url,
        fetch_doaj_pdf_url,
        fetch_europepmc_pdf_url,
    )

    contact_email = context.get("contact_email")
    pdf_url = _call_pdf_resolver(
        fetch_crossref_pdf_url,
        doi,
        contact_email=contact_email if isinstance(contact_email, str) else None,
    )
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "doi"
        return cast(NormalizedRecord, updated)

    pdf_url = fetch_europepmc_pdf_url(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "doi"
        return cast(NormalizedRecord, updated)

    pdf_url = fetch_doaj_pdf_url(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "doi"
        return cast(NormalizedRecord, updated)

    return record


def _call_pdf_resolver(fn, doi: str, *, contact_email: str | None = None) -> str | None:
    if contact_email:
        try:
            return fn(doi, contact_email=contact_email)
        except TypeError:
            return fn(doi)
    return fn(doi)


def unpaywall_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Resolve PDF via Unpaywall OA API."""
    doi = record.get("doi")
    email = context.get("unpaywall_email")
    if not isinstance(doi, str) or not doi.strip() or not email:
        return record

    from pzi.pdf import fetch_unpaywall_pdf_url

    fetch_unpaywall = context.get("fetch_unpaywall") or fetch_unpaywall_pdf_url
    pdf_url = fetch_unpaywall(doi, email=email)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "unpaywall"
        return cast(NormalizedRecord, updated)

    return record


def arxiv_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Build arXiv PDF URL from arxiv_id field."""
    arxiv_id = record.get("arxiv_id")
    if not isinstance(arxiv_id, str) or not arxiv_id.strip():
        return record

    bare = arxiv_id.strip().removeprefix("arXiv:").removeprefix("arxiv:").strip()
    if not bare:
        return record

    updated = dict(record)
    updated["pdf_url"] = f"https://arxiv.org/pdf/{bare}"
    updated["pdf_source"] = "arxiv"
    return cast(NormalizedRecord, updated)


def preprint_pdf_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Build PDF URL for known preprint servers from source/canonical URL."""
    from pzi.promote_service import detect_preprint_source

    landing_url = (
        record.get("source_url")
        or record.get("canonical_url")
        or context.get("raw_value")
    )
    if not isinstance(landing_url, str) or not landing_url.strip():
        return record

    source = detect_preprint_source(record)
    if source is None:
        source = detect_preprint_source({"source_url": landing_url})
    if source is None or source == "arXiv":
        return record  # arXiv handled by arxiv_step

    pdf_url = _build_preprint_pdf_url(source, landing_url)
    if pdf_url is None:
        return record

    updated = dict(record)
    updated["pdf_url"] = pdf_url
    updated["pdf_source"] = "preprint"
    return cast(NormalizedRecord, updated)


def _build_preprint_pdf_url(source: str, landing_url: str) -> str | None:
    """Build a PDF URL for a known preprint server, or None."""
    parts = urlsplit(landing_url)
    path = parts.path.rstrip("/")

    if source in {"bioRxiv", "medRxiv"}:
        # https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1
        # → https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1.full.pdf
        m = _re.search(r"/content/(10\.\d{4,9}/\S+?)(?:v\d+)?$", path)
        if m:
            base_path = f"/content/{m.group(1)}"
            version_match = _re.search(r"(v\d+)$", path)
            version = version_match.group(1) if version_match else ""
            return urlunsplit((
                parts.scheme, parts.hostname or "",
                f"{base_path}{version}.full.pdf", "", ""
            ))

    if source in {"PsyArXiv", "SocArXiv", "engrXiv", "EarthArXiv",
                   "EcoEvoRxiv", "OSF"}:
        # https://osf.io/preprints/psyarxiv/abc123
        # → https://osf.io/preprints/psyarxiv/abc123/download
        return f"{landing_url.rstrip('/')}/download"

    if source == "SSRN":
        # https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567
        # → https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567&download=yes
        abstract_id = _extract_query_param(parts.query, "abstract_id")
        if abstract_id:
            return urlunsplit((
                parts.scheme, parts.hostname or "",
                parts.path, f"abstract_id={abstract_id}&download=yes", ""
            ))

    if source == "HAL":
        # https://hal.science/hal-01234567
        # → https://hal.science/hal-01234567/document
        return f"{landing_url.rstrip('/')}/document"

    if source == "Research Square":
        # https://www.researchsquare.com/article/rs-1234/v1
        # → https://www.researchsquare.com/article/rs-1234/v1.pdf
        return f"{landing_url.rstrip('/')}.pdf"

    if source == "Preprints.org":
        # https://www.preprints.org/manuscript/202401.1234/v1
        # → https://www.preprints.org/manuscript/202401.1234/v1/download
        return f"{landing_url.rstrip('/')}/download"

    if source == "Zenodo":
        # https://zenodo.org/records/1234567
        # → https://zenodo.org/records/1234567/files/paper.pdf (varies)
        # Best effort: the records API returns file URLs, but we can try the record URL
        return None  # Zenodo needs API call to find file URLs

    if source == "ChemRxiv":
        # https://chemrxiv.org/engage/chemrxiv/article-details/123
        # → https://chemrxiv.org/engage/chemrxiv/article-details/123/download
        # The download link format varies; try common pattern
        return f"{landing_url.rstrip('/')}/download"

    if source == "Authorea":
        # https://www.authorea.com/doi/full/10.22541/au.123
        # → https://www.authorea.com/doi/pdf/10.22541/au.123
        return landing_url.replace("/full/", "/pdf/")

    if source == "SAGE Advance":
        # https://advance.sagepub.com/doi/10.31124/123
        # → https://advance.sagepub.com/doi/pdf/10.31124/123
        return landing_url.replace("/doi/10.", "/doi/pdf/10.")

    return None


def _extract_query_param(query: str, key: str) -> str | None:
    """Extract a single query parameter value, or None."""
    from urllib.parse import parse_qs
    values = parse_qs(query).get(key)
    if values:
        return values[0]
    return None


# Canonical fallback chain used by add_service.
DEFAULT_DISCOVERY_STEPS: list[PdfDiscoveryStep] = [
    arxiv_step,                      # 1 — arXiv ID → PDF URL
    preprint_pdf_step,               # 2 — preprint server → PDF URL
    translation_attachment_step,     # 3 — Zotero translator attachments
    web_attachment_step,             # 4 — re-fetch via translation-server /web
    doi_pdf_step,                    # 5 — Crossref / Europe PMC / DOAJ
    unpaywall_step,                  # 6 — Unpaywall OA lookup
    pdf_url_candidates_step,         # 7 — extension-supplied fallback candidates
    browser_pdf_step,                # 8 — Playwright headless browser hook
]
