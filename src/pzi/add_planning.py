"""Pure add/capture planning and metadata fetching helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pzi.bibtex import NormalizedRecord
from pzi.flaresolverr import fetch_html_via_flaresolverr
from pzi.html_metadata import extract_metadata_from_html
from pzi.metadata_sources import (
    fetch_crossref_record,
    fetch_openalex_record,
    fetch_semantic_scholar_record,
)
from pzi.pdf_discovery import (
    DEFAULT_DISCOVERY_STEPS,
    PdfDiscoveryContext,
    apply_pdf_discovery,
)
from pzi.similarity import compute_similarity_hint, find_exact_match


def split_record_overrides(
    record_overrides: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    normal: dict[str, object] = {}
    fallback: dict[str, object] = {}
    for key, value in record_overrides.items():
        if key.startswith("fallback_"):
            fallback[key.removeprefix("fallback_")] = value
        else:
            normal[key] = value
    return normal, fallback


def merge_fetched_record_with_overrides(
    fetched_record: Mapping[str, object], record_overrides: Mapping[str, object]
) -> NormalizedRecord:
    normal, fallback = split_record_overrides(record_overrides)
    merged = dict(fetched_record)
    for key, value in fallback.items():
        if value is None:
            continue
        current = merged.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            merged[key] = value
    return merge_record_sources(merged, normal)


def manual_record_from_overrides(record_overrides: Mapping[str, object]) -> NormalizedRecord:
    normal, fallback = split_record_overrides(record_overrides)
    return merge_record_sources(fallback, normal)


def pdf_result_fields(
    *,
    pdf_url: str | None,
    pdf_path: str | None,
    warnings: list[str],
    dry_run: bool,
) -> dict[str, str | None]:
    """Return structured PDF status fields for add/capture results."""
    if pdf_path is not None:
        return {
            "pdf_url": pdf_url,
            "pdf_status": "direct_saved",
            "pdf_error": None,
            "pdf_suggestion": None,
        }
    if pdf_url is None:
        return {
            "pdf_url": None,
            "pdf_status": "none",
            "pdf_error": None,
            "pdf_suggestion": None,
        }
    if dry_run:
        return {
            "pdf_url": pdf_url,
            "pdf_status": "found",
            "pdf_error": None,
            "pdf_suggestion": None,
        }

    error = warnings[0] if warnings else None
    return {
        "pdf_url": pdf_url,
        "pdf_status": "direct_blocked" if error else "found",
        "pdf_error": error,
        "pdf_suggestion": (
            "Use the browser extension for authenticated/browser-only PDFs, "
            "or configure browser_pdf_cmd."
            if error
            else None
        ),
    }


def _coerce_year(value: object) -> int | None:
    """Coerce a year value (str or int) to int.

    Returns ``None`` when the value cannot be coerced to a plausible year
    (1000–2099 inclusive).
    """
    if isinstance(value, int):
        return value if 1000 <= value <= 2099 else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except (ValueError, OverflowError):
            return None
        if 1000 <= parsed <= 2099:
            return parsed
    return None


def has_minimum_metadata(record: Mapping[str, object]) -> bool:
    """Return True when *record* has sufficient metadata for a fallback add.

    Requires a non-empty title plus at least one of: non-empty DOI,
    non-empty author list, or a plausible numeric year (int or string).
    """
    title = record.get("title")
    doi = record.get("doi")
    authors = record.get("authors")
    year = record.get("year")

    if not isinstance(title, str) or not title.strip():
        return False

    if isinstance(doi, str) and doi.strip():
        return True
    if isinstance(authors, list) and bool(authors):
        return True
    if _coerce_year(year) is not None:
        return True

    return False


def minimum_metadata_diagnostics(record: Mapping[str, object]) -> list[str]:
    """Return human-readable lines explaining why metadata is insufficient."""
    lines: list[str] = []

    title = record.get("title")
    doi = record.get("doi")
    authors = record.get("authors")
    year = record.get("year")

    if not isinstance(title, str) or not title.strip():
        lines.append("missing title: browser extension did not extract page title")
    else:
        contributors: list[str] = []
        if isinstance(doi, str) and doi.strip():
            contributors.append(f"doi={doi.strip()}")
        else:
            contributors.append("doi not available")
        if isinstance(authors, list) and bool(authors):
            contributors.append(f"{len(authors)} author(s)")
        else:
            contributors.append("authors not available")
        if _coerce_year(year) is not None:
            contributors.append(f"year={_coerce_year(year)}")
        else:
            contributors.append("year not available or not numeric")
        lines.append("title found but insufficient identifiers: " + "; ".join(contributors))

    return lines


def error_result(
    *,
    message: str,
    errors: list[str],
    dry_run: bool,
    warnings: list[str],
    bib: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "bib_name": bib["name"] if bib is not None else None,
        "bib_path": bib["path"] if bib is not None else None,
        "action": None,
        "citekey": None,
        "pdf_path": None,
        "changed_fields": [],
        "dry_run": dry_run,
        "message": message,
        "warnings": warnings,
        "errors": errors,
    }


def attach_similarity_hint(
    record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    exact_match_fn=find_exact_match,
    similarity_hint_fn=compute_similarity_hint,
    index: dict | None = None,
) -> NormalizedRecord:
    if exact_match_fn(record, existing_records, index=index) is not None:
        return record

    incoming_citekey = record.get("citekey")
    candidates = [
        existing
        for existing in existing_records
        if existing.get("citekey") != incoming_citekey
    ]
    hint_citekey = similarity_hint_fn(record, candidates)
    if hint_citekey is None:
        return record

    hint_text = f"Possibly similar to {hint_citekey}"
    existing_note = record.get("note")
    if isinstance(existing_note, str) and existing_note.strip():
        if hint_text in existing_note:
            return record
        combined = f"{existing_note.strip()}; {hint_text}"
    else:
        combined = hint_text

    updated = dict(record)
    updated["note"] = combined
    return cast(NormalizedRecord, updated)


# ---------------------------------------------------------------------------
# Metadata fetching pipeline (merged from _record_fetching.py)
# ---------------------------------------------------------------------------


def fetch_record_for_input(
    *,
    raw_value: str,
    classified: Mapping[str, object],
    server_url: str,
    fetch_web,
    fetch_search,
    contact_email: str | None = None,
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
    cookies: str | None = None,
    api_url: str | None = None,
    api_auth_token: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    pdf_discovery_parallel: bool = False,
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
            "contact_email": contact_email,
            "s2_api_key": s2_api_key,
            "flaresolverr_url": flaresolverr_url,
            "browser_pdf_cmd": browser_pdf_cmd,
            "pdf_url_candidates": pdf_url_candidates,
            "cookies": cookies,
            "fetch_web": fetch_web,
            "fetch_unpaywall": fetch_unpaywall,
            "fetch_crossref": fetch_crossref,
            "fetch_openalex": fetch_openalex,
            "fetch_s2": fetch_s2,
            "fetch_flaresolverr": fetch_flaresolverr,
            "translation_attachments": translation_attachments,
            "api_url": api_url,
            "api_auth_token": api_auth_token,
            "desktop_fallback_hosts": desktop_fallback_hosts,
            "pdf_discovery_parallel": pdf_discovery_parallel,
        }

    def _with_pdf_discovery(
        base_record: NormalizedRecord,
        *,
        translation_attachments: list[dict[str, object]] | None = None,
    ) -> NormalizedRecord:
        # Strip bare DOI redirect URLs — they are not downloadable PDFs.
        # Let discovery steps find the actual PDF URL.
        pdf_url = base_record.get("pdf_url")
        if isinstance(pdf_url, str) and pdf_url.startswith("https://doi.org/"):
            base_record = cast(NormalizedRecord, dict(base_record))
            base_record.pop("pdf_url", None)

        if pdf_discovery_parallel:
            from pzi.pdf_discovery import apply_pdf_discovery_parallel as _parallel
            return _parallel(
                base_record,
                DEFAULT_DISCOVERY_STEPS,
                _discovery_context(translation_attachments=translation_attachments),
            )
        return apply_pdf_discovery(
            base_record,
            DEFAULT_DISCOVERY_STEPS,
            _discovery_context(translation_attachments=translation_attachments),
        )

    if kind == "doi" and normalized is not None:
        results = safe_api_call(lambda: fetch_search(normalized, server_url=server_url))
        if results:
            selected = select_best_metadata_result(results, fallback)
            best = dict(merge_record_sources(fallback, selected["record"]))
            return _with_pdf_discovery(
                cast(NormalizedRecord, best), translation_attachments=selected.get("attachments")
            )

        meta = _call_metadata_fetcher(
            fetch_crossref or fetch_crossref_record,
            normalized,
            contact_email=contact_email,
        )
        if meta is None:
            meta = _call_metadata_fetcher(
                fetch_openalex or fetch_openalex_record,
                normalized,
                contact_email=contact_email,
            )
        if meta is None:
            s2_fn = fetch_s2 or (lambda d: fetch_semantic_scholar_record(d, api_key=s2_api_key))
            meta = s2_fn(normalized)
        if meta is not None:
            best = dict(merge_record_sources(fallback, meta))
            return _with_pdf_discovery(cast(NormalizedRecord, best))

        from urllib.parse import urlsplit as _urlsplit

        raw_as_url = (
            raw_value if _urlsplit(raw_value).scheme in {"http", "https"} else None
        )
        if raw_as_url:
            web_results = safe_api_call(
                lambda: fetch_web(raw_as_url, server_url=server_url)
                if cookies is None
                else fetch_web(raw_as_url, server_url=server_url, cookies=cookies)
            )
            if web_results:
                best = dict(
                    merge_record_sources(fallback, web_results[0]["record"])
                )
                return _with_pdf_discovery(
                    cast(NormalizedRecord, best),
                    translation_attachments=web_results[0].get("attachments"),
                )

            if flaresolverr_url is not None:  # pragma: no branch
                fn = fetch_flaresolverr or (
                    lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
                )
                html = fn(raw_as_url)
                if html:  # pragma: no branch — covered by integration/browser tests
                    meta = extract_metadata_from_html(html)
                    if meta is not None:  # pragma: no branch — covered by integration/browser tests
                        best = dict(merge_record_sources(meta, fallback))
                        return _with_pdf_discovery(cast(NormalizedRecord, best))

        suffix = (
            " (page may be Cloudflare-protected — configure flaresolverr_url to bypass)"
            if raw_as_url and flaresolverr_url is None
            else ""
        )
        raise ValueError(f"no metadata found for DOI: {normalized}{suffix}")

    if kind in {"url", "pdf_url"} and normalized is not None:
        results = safe_api_call(
            lambda: fetch_web(normalized, server_url=server_url)
            if cookies is None
            else fetch_web(normalized, server_url=server_url, cookies=cookies)
        )
        if results:
            selected = select_best_metadata_result(results, fallback)
            best = dict(selected["record"])
            best = _with_pdf_discovery(
                cast(NormalizedRecord, best), translation_attachments=selected.get("attachments")
            )
            return merge_record_sources(fallback, best)

        if flaresolverr_url is not None:
            fn = fetch_flaresolverr or (
                lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
            )
            html = fn(normalized)
            if html:
                meta = extract_metadata_from_html(html)
                if meta is not None:
                    best = dict(merge_record_sources(meta, fallback))
                    return _with_pdf_discovery(cast(NormalizedRecord, best))

        raise ValueError(f"translation server returned no results for URL: {normalized}")

    return fallback


def safe_api_call(fn):
    """Run callable, swallowing urllib HTTPError and returning []."""
    import urllib.error

    try:
        return fn()
    except urllib.error.HTTPError:
        return []


def select_best_metadata_result(
    results: list[Mapping[str, Any]], fallback: Mapping[str, object]
) -> Mapping[str, Any]:
    """Choose best metadata result by pure score, preserving input order on ties."""
    if not results:
        raise ValueError("metadata results cannot be empty")
    return max(
        enumerate(results),
        key=lambda item: (score_metadata_candidate(item[1], fallback), -item[0]),
    )[1]


def metadata_result_diagnostics(
    results: list[Mapping[str, Any]], fallback: Mapping[str, object]
) -> list[str]:
    """Pure human-readable diagnostics for metadata result scoring."""
    if not results:
        return []
    scored = [
        (index, result, score_metadata_candidate(result, fallback))
        for index, result in enumerate(results)
    ]
    best_index, best_result, best_score = max(
        scored,
        key=lambda item: (item[2], -item[0]),
    )
    lines = [
        _metadata_diagnostic_line(
            "selected", best_index, len(results), best_score, best_result
        )
    ]
    lines.extend(
        _metadata_diagnostic_line("rejected", index, len(results), score, result)
        for index, result, score in scored
        if index != best_index
    )
    return lines


def metadata_result_confidence_warnings(
    results: list[Mapping[str, Any]],
    fallback: Mapping[str, object],
    *,
    min_score: int = 0,
) -> list[str]:
    """Pure warnings for low-confidence selected metadata results."""
    if not results:
        return []
    selected = select_best_metadata_result(results, fallback)
    score = score_metadata_candidate(selected, fallback)
    if score >= min_score:
        return []
    return [
        "metadata confidence low: "
        f"selected result score={score} below {min_score}; verify captured metadata"
    ]


def score_metadata_candidate(
    result: Mapping[str, Any], fallback: Mapping[str, object]
) -> int:
    """Pure quality score for translation-server metadata candidates."""
    record = result.get("record")
    if not isinstance(record, Mapping):
        return -1000
    score = 0
    score += _identifier_score(record, fallback)
    score += _metadata_richness_score(record)
    score += _attachment_score(result)
    return score


def _identifier_score(record: Mapping[str, object], fallback: Mapping[str, object]) -> int:
    score = 0
    fallback_doi = _norm_text(fallback.get("doi"))
    record_doi = _norm_text(record.get("doi"))
    if fallback_doi and record_doi:
        score += 50 if fallback_doi == record_doi else -50
    fallback_arxiv = _norm_text(fallback.get("arxiv_id"))
    record_arxiv = _norm_text(record.get("arxiv_id"))
    if fallback_arxiv and record_arxiv:
        score += 40 if fallback_arxiv == record_arxiv else -20
    return score


def _metadata_richness_score(record: Mapping[str, object]) -> int:
    score = 0
    for key in ("title", "venue", "doi", "arxiv_id", "abstract_url", "canonical_url"):
        if _norm_text(record.get(key)):
            score += 3
    if isinstance(record.get("year"), int):
        score += 3
    authors = record.get("authors")
    if isinstance(authors, list) and authors:
        score += min(len(authors), 3) * 2
    return score


def _attachment_score(result: Mapping[str, Any]) -> int:
    attachments = result.get("attachments")
    if isinstance(attachments, list) and attachments:
        return 2
    return 0


def _norm_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def _metadata_diagnostic_line(
    status: str,
    index: int,
    total: int,
    score: int,
    result: Mapping[str, Any],
) -> str:
    record = result.get("record")
    if not isinstance(record, Mapping):
        return f"{status} result {index + 1}/{total}: score={score}; invalid record"
    parts = [f"{status} result {index + 1}/{total}: score={score}"]
    doi = record.get("doi")
    title = record.get("title")
    venue = record.get("venue")
    year = record.get("year")
    if isinstance(doi, str) and doi.strip():
        parts.append(f"doi={doi.strip()}")
    if isinstance(title, str) and title.strip():
        parts.append(f"title={title.strip()}")
    if isinstance(venue, str) and venue.strip():
        parts.append(f"venue={venue.strip()}")
    if isinstance(year, int):
        parts.append(f"year={year}")
    return "; ".join(parts)


def _call_metadata_fetcher(fn, doi: str, *, contact_email: str | None):
    if contact_email:
        try:
            return fn(doi, contact_email=contact_email)
        except TypeError:
            return fn(doi)
    return fn(doi)


def _fallback_record_for_input(
    *, kind: str, normalized: str | None, raw_value: str
) -> NormalizedRecord:
    if kind == "doi" and normalized is not None:
        record: NormalizedRecord = {"doi": normalized}
        raw_as_url = raw_value if _url_is_http(raw_value) else None
        if raw_as_url is not None:
            record["canonical_url"] = raw_as_url
            record["source_url"] = raw_as_url
            record["abstract_url"] = raw_as_url
        arxiv_id = _arxiv_id_from_doi(normalized)
        if arxiv_id is not None:
            record["arxiv_id"] = arxiv_id
        return record
    if kind == "pdf_url" and normalized is not None:
        return {"pdf_url": normalized, "source_url": normalized}
    if kind == "url" and normalized is not None:
        return {
            "canonical_url": normalized,
            "source_url": normalized,
            "abstract_url": normalized,
        }
    return {"source_url": raw_value}


def _url_is_http(value: str) -> bool:
    from urllib.parse import urlsplit as _urlsplit

    try:
        return _urlsplit(value).scheme in {"http", "https"}
    except ValueError:
        return False


def _arxiv_id_from_doi(doi: str) -> str | None:
    """Extract bare arXiv ID from an arXiv DOI like 10.48550/arXiv.1106.5249."""
    lower = doi.lower()
    prefix = "10.48550/arxiv."
    if lower.startswith(prefix):
        return doi[len(prefix):]
    return None


def merge_record_sources(
    base: Mapping[str, object], overrides: Mapping[str, object]
) -> NormalizedRecord:
    merged = dict(base)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return cast(NormalizedRecord, merged)

