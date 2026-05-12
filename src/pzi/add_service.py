"""Add/capture workflow orchestration."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping
from typing import Any, TypeAlias, cast

from pzi.bib_repository import execute_write_plan, read_bib_file
from pzi.bibtex import NormalizedRecord
from pzi.citekeys import generate_citekey
from pzi.config import BibConfig, resolve_bib
from pzi.config_loader import load_config_file
from pzi.crossref import fetch_crossref_record
from pzi.flaresolverr import fetch_html_via_flaresolverr
from pzi.html_metadata import extract_metadata_from_html
from pzi.identifiers import classify_input
from pzi.identity import find_exact_match
from pzi.openalex import fetch_openalex_record
from pzi.pdf import copy_pdf_to_papers_dir, fetch_and_store_pdf_with_fallbacks
from pzi.pdf_discovery import (
    DEFAULT_DISCOVERY_STEPS,
    PdfDiscoveryContext,
    apply_pdf_discovery,
)
from pzi.pdf_metadata import extract_pdf_metadata
from pzi.semantic_scholar import fetch_semantic_scholar_record
from pzi.similarity import compute_similarity_hint
from pzi.translation_server import (
    fetch_search_translations,
    fetch_web_translations,
)
from pzi.write_plan import plan_bib_write

AddRecordResult: TypeAlias = dict[str, Any]



def add_input_to_bib(
    *,
    config_path: str,
    home_dir: str,
    value: str,
    record_overrides: dict[str, object],
    bib_selector: str | None,
    dry_run: bool,
    fetch_web=fetch_web_translations,
    fetch_search=fetch_search_translations,
    fetch_binary=None,
    fetch_unpaywall=None,
    fetch_crossref=None,
    fetch_openalex=None,
    fetch_s2=None,
    fetch_flaresolverr=None,
    pdf_url_candidates: list[str] | None = None,
    browser_pdf_cmd: str | None = None,
) -> AddRecordResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return _error_result(
            message="failed to load config",
            errors=config_result["errors"],
            dry_run=dry_run,
            warnings=[],
        )

    config = config_result["config"]
    bib = resolve_bib(config["bibs"], bib_selector)
    if bib is None:
        return _error_result(
            message="could not resolve target bib",
            errors=["no matching bib found or selection is ambiguous"],
            dry_run=dry_run,
            warnings=[],
        )

    cmd_email = config["unpaywall_email_cmd"]
    unpaywall_email = (
        subprocess.run(
            shlex.split(cmd_email), capture_output=True, text=True
        ).stdout.strip()
        or None
        if cmd_email
        else config["unpaywall_email"]
    )
    cmd_s2 = config["semantic_scholar_api_key_cmd"]
    s2_api_key = (
        subprocess.run(
            shlex.split(cmd_s2), capture_output=True, text=True
        ).stdout.strip()
        or None
        if cmd_s2
        else config["semantic_scholar_api_key"]
    )

    classified = classify_input(value)
    if classified["kind"] == "local_pdf":
        return _add_local_pdf(
            bib=bib,
            raw_value=value,
            record_overrides=record_overrides,
            dry_run=dry_run,
            server_url=config["translation_server_url"],
            fetch_search=fetch_search,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_s2=fetch_s2,
            s2_api_key=s2_api_key,
            fetch_web=fetch_web,
            flaresolverr_url=config.get("flaresolverr_url"),
            browser_pdf_cmd=config.get("browser_pdf_cmd"),
        )

    try:
        fetched_record = _fetch_record_for_input(
            raw_value=value,
            classified=classified,
            server_url=config["translation_server_url"],
            fetch_web=fetch_web,
            fetch_search=fetch_search,
            unpaywall_email=unpaywall_email,
            s2_api_key=s2_api_key,
            flaresolverr_url=config.get("flaresolverr_url"),
            fetch_unpaywall=fetch_unpaywall,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_s2=fetch_s2,
            fetch_flaresolverr=fetch_flaresolverr,
            pdf_url_candidates=pdf_url_candidates,
            browser_pdf_cmd=config.get("browser_pdf_cmd"),
        )
    except Exception as exc:
        manual_record = _merge_record_sources({}, record_overrides)
        title = manual_record.get("title")
        doi = manual_record.get("doi")
        authors = manual_record.get("authors")
        year = manual_record.get("year")
        has_min_meta = (
            isinstance(title, str)
            and bool(title.strip())
            and (
                (isinstance(doi, str) and bool(doi.strip()))
                or (isinstance(authors, list) and bool(authors))
                or isinstance(year, int)
            )
        )
        if classified["kind"] in {"doi", "url", "pdf_url"} and has_min_meta:
            return _add_record_with_bib(
                bib=bib,
                record=_merge_record_sources(
                    _fallback_record_for_input(
                        kind=cast(str, classified["kind"]),
                        normalized=cast(str | None, classified["normalized"]),
                        raw_value=value,
                    ),
                    manual_record,
                ),
                dry_run=dry_run,
                fetch_binary=fetch_binary,
                flaresolverr_url=config.get("flaresolverr_url"),
                browser_pdf_cmd=config.get("browser_pdf_cmd"),
            )
        import urllib.error
        from urllib.parse import urlsplit

        is_conn_err = (
            isinstance(exc, (urllib.error.URLError, ConnectionError, OSError))
            and not isinstance(exc, urllib.error.HTTPError)
        )
        if is_conn_err:
            parts = urlsplit(config["translation_server_url"])
            host_port = parts.port or 1969
            err_msg = (
                f"translation server not reachable at {config['translation_server_url']} — "
                f"start it with: podman run -p {host_port}:1969 translation-server"
            )
        else:
            err_msg = str(exc)
        return _error_result(
            message="translation server error",
            errors=[err_msg],
            dry_run=dry_run,
            warnings=[],
        )

    merged_record = _merge_record_sources(fetched_record, record_overrides)
    return _add_record_with_bib(
        bib=bib,
        record=merged_record,
        dry_run=dry_run,
        fetch_binary=fetch_binary,
        flaresolverr_url=config.get("flaresolverr_url"),
        browser_pdf_cmd=config.get("browser_pdf_cmd"),
    )


def add_record_to_bib(
    *,
    config_path: str,
    home_dir: str,
    record: dict[str, object],
    bib_selector: str | None,
    dry_run: bool,
) -> AddRecordResult:
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return _error_result(
            message="failed to load config",
            errors=config_result["errors"],
            dry_run=dry_run,
            warnings=[],
        )

    config = config_result["config"]
    bib = resolve_bib(config["bibs"], bib_selector)
    if bib is None:
        return _error_result(
            message="could not resolve target bib",
            errors=["no matching bib found or selection is ambiguous"],
            dry_run=dry_run,
            warnings=[],
        )

    return _add_record_with_bib(bib=bib, record=record, dry_run=dry_run)


def _add_record_with_bib(
    *,
    bib: BibConfig,
    record: Mapping[str, object],
    dry_run: bool,
    fetch_binary=None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
) -> AddRecordResult:
    read_result = read_bib_file(bib["path"])
    typed_existing_records = [
        cast(NormalizedRecord, existing) for existing in read_result["records"]
    ]
    typed_record = _ensure_citekey(
        cast(NormalizedRecord, dict(record)), typed_existing_records
    )
    record_with_pdf, warnings = _attach_pdf_if_available(
        record=typed_record,
        bib=bib,
        dry_run=dry_run,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
    )
    record_with_hint = _attach_similarity_hint(record_with_pdf, typed_existing_records)
    plan = plan_bib_write(record_with_hint, typed_existing_records)

    if not dry_run:
        execute_write_plan(bib["path"], plan)

    citekey = plan["record"].get("citekey")
    pdf_path = plan["record"].get("local_pdf_path")
    prefix = "would " if dry_run else ""
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "bib_path": bib["path"],
        "action": plan["action"],
        "citekey": citekey if isinstance(citekey, str) else None,
        "pdf_path": pdf_path if isinstance(pdf_path, str) else None,
        "changed_fields": plan["changed_fields"],
        "dry_run": dry_run,
        "message": f"{prefix}{plan['action']} entry",
        "warnings": warnings,
        "errors": [],
    }


def _fetch_record_for_input(
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

            if flaresolverr_url is not None:
                fn = fetch_flaresolverr or (
                    lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
                )
                html = fn(raw_as_url)
                if html:
                    meta = extract_metadata_from_html(html)
                    if meta is not None:
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
            return _merge_record_sources(best, fallback)

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


def _safe_call(fn):
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


def _merge_record_sources(
    base: Mapping[str, object], overrides: Mapping[str, object]
) -> NormalizedRecord:
    merged = dict(base)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    return cast(NormalizedRecord, merged)


def _ensure_citekey(
    record: NormalizedRecord, existing_records: list[NormalizedRecord]
) -> NormalizedRecord:
    ck = record.get("citekey")
    if isinstance(ck, str) and bool(ck.strip()):
        return record

    generated = dict(record)
    existing_keys = {
        citekey
        for existing in existing_records
        for citekey in [existing.get("citekey")]
        if isinstance(citekey, str) and citekey.strip()
    }
    generated["citekey"] = generate_citekey(
        {
            "authors": list(record.get("authors") or []),
            "title": cast(str | None, record.get("title")),
            "year": cast(int | None, record.get("year")),
        },
        existing_keys,
    )
    return cast(NormalizedRecord, generated)


def _add_local_pdf(
    *,
    bib: BibConfig,
    raw_value: str,
    record_overrides: dict[str, object],
    dry_run: bool,
    server_url: str,
    fetch_search,
    fetch_web=fetch_web_translations,
    fetch_crossref=None,
    fetch_openalex=None,
    fetch_s2=None,
    s2_api_key: str | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
) -> AddRecordResult:
    """Ingest a local PDF: extract metadata, resolve, copy PDF, add to bib."""
    read_result = read_bib_file(bib["path"])
    existing_records = [
        cast(NormalizedRecord, r) for r in read_result["records"]
    ]

    extracted = extract_pdf_metadata(raw_value)
    base_record: NormalizedRecord

    doi = extracted.get("doi")
    if isinstance(doi, str) and doi.strip():
        # Treat as DOI lookup
        try:
            base_record = _fetch_record_for_input(
                raw_value=doi,
                classified={"kind": "doi", "raw": doi, "normalized": doi},
                server_url=server_url,
                fetch_web=fetch_web,
                fetch_search=fetch_search,
                fetch_crossref=fetch_crossref,
                fetch_openalex=fetch_openalex,
                fetch_s2=fetch_s2,
                s2_api_key=s2_api_key,
                flaresolverr_url=flaresolverr_url,
                browser_pdf_cmd=browser_pdf_cmd,
            )
        except (OSError, ValueError):
            base_record = {"doi": doi, "source_url": raw_value}
    else:
        title = extracted.get("title")
        if isinstance(title, str) and title.strip():
            try:
                results = fetch_search(title, server_url=server_url)
            except (OSError, ValueError):
                results = []
            if results:
                base_record = dict(results[0]["record"])
            else:
                base_record = {"title": title.strip(), "source_url": raw_value}
        else:
            base_record = {"source_url": raw_value}

    merged = _merge_record_sources(base_record, record_overrides)
    record_with_ck = _ensure_citekey(merged, existing_records)
    citekey = record_with_ck.get("citekey")

    warnings: list[str] = []

    if not dry_run and isinstance(citekey, str) and citekey.strip():
        local_path, error = copy_pdf_to_papers_dir(
            source_path=raw_value,
            papers_dir=bib["papers_dir"],
            citekey=citekey,
        )
        if error is not None:
            warnings.append(error)
        elif local_path is not None:
            record_with_ck = dict(record_with_ck)
            record_with_ck["local_pdf_path"] = local_path

    result = _add_record_with_bib(
        bib=bib,
        record=record_with_ck,
        dry_run=dry_run,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
    )
    result["warnings"] = [*warnings, *result["warnings"]]
    return result


def _error_result(
    *,
    message: str,
    errors: list[str],
    dry_run: bool,
    warnings: list[str],
    bib: BibConfig | None = None,
) -> AddRecordResult:
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


def _attach_pdf_if_available(
    *,
    record: NormalizedRecord,
    bib: BibConfig,
    dry_run: bool,
    fetch_binary,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
) -> tuple[NormalizedRecord, list[str]]:
    pdf_url = record.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip():
        return record, []

    if record.get("local_pdf_path"):
        return record, []

    if dry_run:
        return record, []

    citekey = record.get("citekey")
    if not isinstance(citekey, str) or not citekey.strip():
        return record, ["cannot attach PDF before citekey generation"]

    local_pdf_path, warning, error = fetch_and_store_pdf_with_fallbacks(
        url=pdf_url,
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
    )
    if local_pdf_path is None:
        return record, [error] if error is not None else []

    updated = dict(record)
    updated["local_pdf_path"] = local_pdf_path
    warnings: list[str] = []
    if warning is not None:
        warnings.append(warning)
    return cast(NormalizedRecord, updated), warnings


def _attach_similarity_hint(
    record: NormalizedRecord, existing_records: list[NormalizedRecord]
) -> NormalizedRecord:
    if find_exact_match(record, existing_records) is not None:
        return record

    incoming_citekey = record.get("citekey")
    candidates = [
        existing
        for existing in existing_records
        if existing.get("citekey") != incoming_citekey
    ]
    hint_citekey = compute_similarity_hint(record, candidates)
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
