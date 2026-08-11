"""Composable PDF URL discovery pipeline.

Each step has the same shape — ``(record, context) -> record`` — and steps are
composed into a fallback chain that runs until ``pdf_url`` is found.

**Steps are not all pure.** Each declares its execution phase with
:func:`discovery_phase`: ``"pure"`` steps only rearrange what the record already
holds, ``"http"`` steps make network calls (and are run concurrently), and the
``"browser"`` step launches a headless browser as the last resort. The phase is
data on the step, never inferred from its name.

Also includes PDF candidate extraction helpers.
"""

from __future__ import annotations

import re as _re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast
from urllib.parse import urlsplit, urlunsplit

from pzi.bibtex import NormalizedRecord
from pzi.identifiers import detect_preprint_source
from pzi.protocols import accepts_keyword
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


# ---------------------------------------------------------------------------
# Discovery pipeline
# ---------------------------------------------------------------------------



DiscoveryPhase = Literal["pure", "http", "browser"]

# Phase 2 (parallel HTTP) is the default: a step that does not declare itself is
# assumed to touch the network, which is the safe assumption for scheduling.
_DEFAULT_PHASE: DiscoveryPhase = "http"


def discovery_phase(phase: DiscoveryPhase):
    """Declare which execution phase a discovery step belongs to.

    The phase is data attached to the step, not inferred from its ``__name__``.
    The old scheduler string-matched function names, so renaming a step silently
    moved it to a different phase — pure steps would start doing network work in
    the parallel pool, and the browser fallback could stop running last.
    """

    def mark(step: PdfDiscoveryStep) -> PdfDiscoveryStep:
        step.discovery_phase = phase  # type: ignore[attr-defined]
        return step

    return mark


def phase_of(step: PdfDiscoveryStep) -> DiscoveryPhase:
    """Return the declared phase of *step* (``"http"`` when undeclared)."""
    return getattr(step, "discovery_phase", _DEFAULT_PHASE)


def apply_pdf_discovery(
    record: NormalizedRecord,
    steps: list[PdfDiscoveryStep],
    context: PdfDiscoveryContext,
) -> NormalizedRecord:
    """Run PDF discovery steps in order until pdf_url is populated.

    A step that raises (network, parse, provider error) is treated as "no
    result" and the chain continues to the next step, matching
    ``apply_pdf_discovery_parallel``'s per-step isolation — a single failing
    source must not abort the whole add.

    The incoming record is validated before the loop, not only each step's
    result: on a re-run after a failed download it still carries the URL that
    failed, and the ``if record.get("pdf_url")`` guard would otherwise return it
    again untouched without running a single step.
    """
    record = _validated_discovery(record, context)
    for step in steps:
        if record.get("pdf_url"):
            break
        try:
            record = _validated_discovery(step(record, context), context)
        except Exception as exc:
            record_discovery_failure(context, step, exc)
            continue
    return record


def record_discovery_failure(
    context: PdfDiscoveryContext, step: PdfDiscoveryStep, exc: BaseException
) -> None:
    """Note which discovery step failed and why, without stopping the fan-out.

    Both entry points swallow a raising step as "no result", which is right — a
    single failing source must not abort the whole add. But nothing recorded
    *which* step failed, so a permanently broken provider (an expired Unpaywall
    email, a changed API shape) was indistinguishable from "this paper has no
    open-access copy" for as long as it stayed broken. The context is a plain
    dict the caller already holds, so the diagnostics come back with it.
    """
    name = getattr(step, "__name__", None) or repr(step)
    context.setdefault("discovery_diagnostics", []).append(f"{name}: {exc!r}")


def discovery_diagnostics(context: PdfDiscoveryContext | None) -> list[str]:
    """Discovery steps that raised during this record's fan-out, in order."""
    if not isinstance(context, dict):
        return []
    entries = context.get("discovery_diagnostics")
    return [str(item) for item in entries] if isinstance(entries, list) else []


def excluded_pdf_urls(context: PdfDiscoveryContext | None) -> frozenset[str]:
    """URLs a caller has already tried and does not want offered again.

    The download stage puts a candidate here once every transport fallback has
    failed on it, so that re-running discovery yields the *next* source rather
    than the same dead URL. Without this, discovery is deterministic and a
    second run returns exactly what the first one did.
    """
    raw = (context or {}).get("exclude_pdf_urls")
    if not raw:
        return frozenset()
    return frozenset(str(url) for url in raw)


def _validated_discovery(
    record: NormalizedRecord, context: PdfDiscoveryContext | None = None
) -> NormalizedRecord:
    """Drop a ``pdf_url`` that is not a public http(s) URL, or is excluded.

    Only the browser step checked what it had found. Every other step takes its
    URL from a provider response or from the captured page, and the server then
    *fetches* it — or puts it in the plan the extension executes with the user's
    cookies. Either way an attacker-supplied `http://169.254.169.254/…` or
    `file:///…` was followed.

    Enforced here, at the one place a step's result is accepted, so a step added
    later is covered without having to remember. The caller's exclusion list is
    applied in the same place and for the same reason: a step that happens to
    rediscover an already-failed URL must not end the chain with it.
    """
    pdf_url = record.get("pdf_url")
    if not pdf_url:
        return record
    if _safe_public_http_url(str(pdf_url)) and str(pdf_url) not in excluded_pdf_urls(context):
        return record
    cleaned = dict(record)
    cleaned.pop("pdf_url", None)
    cleaned.pop("pdf_source", None)
    return cast(NormalizedRecord, cleaned)


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
    # Phase 1: run pure/fast steps sequentially. The incoming record is
    # validated first for the same reason as in ``apply_pdf_discovery``: after a
    # failed download it still carries the URL that failed.
    record = _validated_discovery(record, context)
    for step in steps:
        if record.get("pdf_url"):
            return record
        if phase_of(step) == "pure":
            record = _validated_discovery(step(record, context), context)

    if record.get("pdf_url"):
        return record

    # Phase 2: run HTTP steps in parallel
    http_steps = [step for step in steps if phase_of(step) == "http"]
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
                    results[step] = _validated_discovery(future.result(), context)
                except Exception as exc:
                    record_discovery_failure(context, step, exc)
                    # A single discovery source failing (network, parse, provider
                    # error) must not abort the whole fan-out: treat it as "no
                    # result" so lower-priority sources still get their turn.
                    results[step] = None
        for step in http_steps:
            result = results.get(step)
            if result is not None and result.get("pdf_url"):
                return result

    # Phase 3: browser fallback
    for step in steps:
        if record.get("pdf_url"):
            return record
        if phase_of(step) == "browser":
            record = _validated_discovery(step(record, context), context)

    return record


@discovery_phase("pure")
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


@discovery_phase("pure")
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


def cookies_for_url(context: PdfDiscoveryContext, url: str) -> str | None:
    """The `--cookie-file` cookies, but only for the origin they belong to.

    One cookie string was reused across every landing-page candidate, and those
    candidates come from provider-supplied `canonical_url` and `source_url` as
    well as from what the user typed — so a session cookie captured for the
    user's institutional proxy was sent to whatever host a metadata record
    happened to name. The cookies were captured while the user was on *their*
    URL, so that origin is the only one they are scoped to.
    """
    cookies = context.get("cookies")
    if not isinstance(cookies, str) or not cookies.strip():
        return None
    origin = context.get("cookie_origin")
    if not isinstance(origin, str) or not origin:
        raw = context.get("raw_value")
        origin = _origin_of(raw) if isinstance(raw, str) else None
    if not origin or _origin_of(url) != origin:
        return None
    return cookies


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}"


@discovery_phase("http")
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
            cookies = cookies_for_url(context, url)
            if cookies is not None:
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


@discovery_phase("browser")
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


@discovery_phase("http")
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

    # Honor injected seams (tests, alternate providers) the same way
    # unpaywall_step does, falling back to the real network fetchers.
    fetch_crossref_pdf = context.get("fetch_crossref_pdf") or fetch_crossref_pdf_url
    fetch_europepmc_pdf = context.get("fetch_europepmc_pdf") or fetch_europepmc_pdf_url
    fetch_doaj_pdf = context.get("fetch_doaj_pdf") or fetch_doaj_pdf_url

    contact_email = context.get("contact_email")
    pdf_url = _call_pdf_resolver(
        fetch_crossref_pdf,
        doi,
        contact_email=contact_email if isinstance(contact_email, str) else None,
    )
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "doi"
        return cast(NormalizedRecord, updated)

    pdf_url = fetch_europepmc_pdf(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "doi"
        return cast(NormalizedRecord, updated)

    pdf_url = fetch_doaj_pdf(doi)
    if pdf_url:
        updated = dict(record)
        updated["pdf_url"] = pdf_url
        updated["pdf_source"] = "doi"
        return cast(NormalizedRecord, updated)

    return record


def _call_pdf_resolver(fn, doi: str, *, contact_email: str | None = None) -> str | None:
    if contact_email and accepts_keyword(fn, "contact_email"):
        return fn(doi, contact_email=contact_email)
    return fn(doi)


@discovery_phase("http")
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


@discovery_phase("pure")
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


@discovery_phase("pure")
def preprint_pdf_step(
    record: NormalizedRecord, context: PdfDiscoveryContext
) -> NormalizedRecord:
    """Build PDF URL for known preprint servers from source/canonical URL."""

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
        if "/full/" not in landing_url:
            return None
        return landing_url.replace("/full/", "/pdf/")

    if source == "SAGE Advance":
        # https://advance.sagepub.com/doi/10.31124/123
        # → https://advance.sagepub.com/doi/pdf/10.31124/123
        if "/doi/10." not in landing_url:
            return None
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
