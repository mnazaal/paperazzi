"""Pure, composable PDF URL discovery pipeline.

Each step is a pure function  (record, context) -> record.
Steps are composed into a fallback chain that runs until pdf_url is found.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeAlias

from pzi.bibtex import NormalizedRecord

PdfDiscoveryContext: TypeAlias = dict[str, Any]



PdfDiscoveryStep = Callable[[NormalizedRecord, PdfDiscoveryContext], NormalizedRecord]


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


def translation_attachment_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Use PDF URL from translation-server attachment list."""
    attachments = context.get("translation_attachments")
    if not attachments:
        return record

    attachment = attachments[0]
    if not isinstance(attachment, Mapping):
        return record

    url = attachment.get("url")
    if not isinstance(url, str) or not url.strip():
        return record

    updated = dict(record)
    updated["pdf_url"] = url.strip()
    return updated


def pdf_url_candidates_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Use first non-empty candidate from externally-supplied list."""
    candidates = context.get("pdf_url_candidates")
    if not candidates:
        return record

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            updated = dict(record)
            updated["pdf_url"] = candidate.strip()
            return updated

    return record


def web_attachment_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Fetch landing pages via translation-server /web and use first PDF attachment.

    Also backfills canonical_url / source_url / abstract_url if the translator
    result provides them and the record currently lacks them.
    """
    from pzi.pdf_acquisition import landing_page_urls

    fetch_web = context["fetch_web"]
    candidate_urls = landing_page_urls(base_record=record, raw_value=context["raw_value"])

    for url in candidate_urls:
        try:
            results = fetch_web(url, server_url=context["server_url"])
        except (OSError, ValueError):
            continue

        for result in results:
            attachments = result.get("attachments")
            if not isinstance(attachments, list) or not attachments:
                continue

            attachment = attachments[0]
            if not isinstance(attachment, Mapping):
                continue

            pdf_url = attachment.get("url")
            if not isinstance(pdf_url, str) or not pdf_url.strip():
                continue

            updated = dict(record)
            updated["pdf_url"] = pdf_url.strip()

            result_record = result.get("record")
            if isinstance(result_record, Mapping):  # pragma: no branch — covered by integration/browser tests
                for key in ("canonical_url", "source_url", "abstract_url"):
                    value = result_record.get(key)
                    if (
                        isinstance(value, str)
                        and value.strip()
                        and not record.get(key)
                    ):
                        updated[key] = value

            return updated

    return record


def browser_pdf_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Discover PDF URL using external browser hook command."""
    browser_pdf_cmd = context.get("browser_pdf_cmd")
    if browser_pdf_cmd is None:
        return record

    from pzi.browser_pdf import discover_pdf_url_with_browser
    from pzi.pdf_acquisition import landing_page_urls

    doi = record.get("doi") if isinstance(record.get("doi"), str) else None
    for url in landing_page_urls(base_record=record, raw_value=context["raw_value"]):
        pdf_url = discover_pdf_url_with_browser(
            command=browser_pdf_cmd,
            page_url=url,
            doi=doi,
        )
        if pdf_url:  # pragma: no branch — covered by integration/browser tests
            updated = dict(record)
            updated["pdf_url"] = pdf_url
            return updated

    return record


def doi_pdf_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Resolve PDF via Crossref, Europe PMC, and DOAJ."""
    doi = record.get("doi")
    if not isinstance(doi, str) or not doi.strip():
        return record

    from pzi.crossref import fetch_crossref_pdf_url
    from pzi.doaj import fetch_doaj_pdf_url
    from pzi.europepmc import fetch_europepmc_pdf_url

    pdf_url = fetch_crossref_pdf_url(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        return updated

    pdf_url = fetch_europepmc_pdf_url(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        return updated

    pdf_url = fetch_doaj_pdf_url(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        return updated

    return record


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
        return updated

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
    return updated


# Canonical fallback chain used by add_service.
DEFAULT_DISCOVERY_STEPS: list[PdfDiscoveryStep] = [
    translation_attachment_step,
    pdf_url_candidates_step,
    web_attachment_step,
    browser_pdf_step,
    doi_pdf_step,
    unpaywall_step,
    arxiv_step,
]
