"""Add/capture workflow orchestration.

Public API:
    add_input_to_bib  — main entry point for CLI / HTTP capture
    add_record_to_bib — capture a pre-built record

Metadata fetching is delegated to _record_fetching.py.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping
from typing import Any, TypeAlias, cast

from pzi._record_fetching import (
    _fallback_record_for_input,
    _fetch_record_for_input,
    _merge_record_sources,
    _safe_call,
)
from pzi.bib_repository import execute_write_plan, plan_bib_write, read_bib_file
from pzi.bibtex import BibtexEntry, NormalizedRecord, generate_citekey
from pzi.config import BibConfig, load_config_file, resolve_bib
from pzi.identifiers import classify_input
from pzi.pdf import copy_pdf_to_papers_dir, fetch_and_store_pdf_with_fallbacks
from pzi.pdf_discovery import DEFAULT_DISCOVERY_STEPS, apply_pdf_discovery
from pzi.pdf_service import extract_pdf_metadata
from pzi.similarity import compute_similarity_hint, find_exact_match
from pzi.translation_server import (
    fetch_search_translations,
    fetch_web_translations,
)

AddRecordResult: TypeAlias = dict[str, Any]
CaptureContext: TypeAlias = dict[str, Any]



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
    context, context_error = _resolve_capture_context(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        dry_run=dry_run,
        browser_pdf_cmd_override=browser_pdf_cmd,
    )
    if context_error is not None:
        return context_error
    assert context is not None

    config = context["config"]
    bib = context["bib"]
    unpaywall_email = context["unpaywall_email"]
    s2_api_key = context["s2_api_key"]
    effective_browser_pdf_cmd = context["browser_pdf_cmd"]

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
            browser_pdf_cmd=effective_browser_pdf_cmd,
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
            browser_pdf_cmd=effective_browser_pdf_cmd,
        )
    except Exception as exc:
        manual_record = _manual_record_from_overrides(record_overrides)
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
            fallback_record = _merge_record_sources(
                _fallback_record_for_input(
                    kind=cast(str, classified["kind"]),
                    normalized=cast(str | None, classified["normalized"]),
                    raw_value=value,
                ),
                manual_record,
            )
            fallback_record = apply_pdf_discovery(
                fallback_record,
                DEFAULT_DISCOVERY_STEPS,
                {
                    "raw_value": value,
                    "server_url": config["translation_server_url"],
                    "unpaywall_email": unpaywall_email,
                    "s2_api_key": s2_api_key,
                    "flaresolverr_url": config.get("flaresolverr_url"),
                    "browser_pdf_cmd": effective_browser_pdf_cmd,
                    "pdf_url_candidates": pdf_url_candidates,
                    "fetch_web": fetch_web,
                    "fetch_unpaywall": fetch_unpaywall,
                    "fetch_crossref": fetch_crossref,
                    "fetch_openalex": fetch_openalex,
                    "fetch_s2": fetch_s2,
                    "fetch_flaresolverr": fetch_flaresolverr,
                    "translation_attachments": None,
                },
            )
            return _add_record_with_bib(
                bib=bib,
                record=fallback_record,
                dry_run=dry_run,
                fetch_binary=fetch_binary,
                flaresolverr_url=config.get("flaresolverr_url"),
                browser_pdf_cmd=effective_browser_pdf_cmd,
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

    merged_record = _merge_fetched_record_with_overrides(fetched_record, record_overrides)
    return _add_record_with_bib(
        bib=bib,
        record=merged_record,
        dry_run=dry_run,
        fetch_binary=fetch_binary,
        flaresolverr_url=config.get("flaresolverr_url"),
        browser_pdf_cmd=effective_browser_pdf_cmd,
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_capture_context(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    dry_run: bool,
    browser_pdf_cmd_override: str | None,
) -> tuple[CaptureContext | None, AddRecordResult | None]:
    """Load config, select the bib, and resolve runtime-only capture options."""
    config_result = load_config_file(config_path, home_dir=home_dir)
    if config_result["config"] is None:
        return None, _error_result(
            message="failed to load config",
            errors=config_result["errors"],
            dry_run=dry_run,
            warnings=[],
        )

    config = config_result["config"]
    bib = resolve_bib(config["bibs"], bib_selector)
    if bib is None:
        return None, _error_result(
            message="could not resolve target bib",
            errors=["no matching bib found or selection is ambiguous"],
            dry_run=dry_run,
            warnings=[],
        )

    return {
        "config": config,
        "bib": bib,
        "unpaywall_email": _resolve_optional_command(
            config["unpaywall_email_cmd"], config["unpaywall_email"]
        ),
        "s2_api_key": _resolve_optional_command(
            config["semantic_scholar_api_key_cmd"], config["semantic_scholar_api_key"]
        ),
        "browser_pdf_cmd": browser_pdf_cmd_override or config.get("browser_pdf_cmd"),
    }, None


def _resolve_optional_command(command: str | None, fallback: str | None) -> str | None:
    if not command:
        return fallback
    return subprocess.run(
        shlex.split(command), capture_output=True, text=True
    ).stdout.strip() or None


def _split_record_overrides(
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


def _merge_fetched_record_with_overrides(
    fetched_record: Mapping[str, object], record_overrides: Mapping[str, object]
) -> NormalizedRecord:
    normal, fallback = _split_record_overrides(record_overrides)
    merged = dict(fetched_record)
    for key, value in fallback.items():
        if value is None:
            continue
        current = merged.get(key)
        if current is None or (isinstance(current, str) and not current.strip()):
            merged[key] = value
    return _merge_record_sources(merged, normal)


def _manual_record_from_overrides(record_overrides: Mapping[str, object]) -> NormalizedRecord:
    normal, fallback = _split_record_overrides(record_overrides)
    return _merge_record_sources(fallback, normal)


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
    typed_record = _ensure_citekey_for_write(
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
    result: AddRecordResult = {
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

    if dry_run:
        result["diff"] = _dry_run_diff(
            plan=plan, existing_entries=read_result["entries"]
        )

    return result


def _ensure_citekey_for_write(
    record: NormalizedRecord, existing_records: list[NormalizedRecord]
) -> NormalizedRecord:
    match_index = find_exact_match(record, existing_records)
    if match_index is not None:
        existing_citekey = existing_records[match_index].get("citekey")
        if isinstance(existing_citekey, str) and existing_citekey.strip():
            matched = dict(record)
            matched["citekey"] = existing_citekey
            return cast(NormalizedRecord, matched)

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


_ensure_citekey = _ensure_citekey_for_write


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



# Re-exports from _record_fetching for test monkeypatching
_fetch_record_for_input = _fetch_record_for_input
_merge_record_sources = _merge_record_sources
_safe_call = _safe_call
_fallback_record_for_input = _fallback_record_for_input


def _dry_run_diff(
    *,
    plan: dict[str, Any],
    existing_entries: list[BibtexEntry],
) -> str:
    """Return a human-readable diff of what the write plan would change."""
    from pzi.bib_repository import serialize_bibtex

    new_entry = plan["entry"]
    new_text = serialize_bibtex([new_entry]).rstrip()

    if plan["action"] == "insert":
        action = "new entry"
        old_text = "(none — new entry)"
    else:
        action = "update entry"
        index = plan["index"]
        try:
            old_entry = existing_entries[index] if index is not None else None
        except IndexError:
            old_entry = None
        if old_entry is None:
            old_text = "(original entry not available)"
        else:
            old_text = serialize_bibtex([old_entry]).rstrip()

    changed = ", ".join(plan["changed_fields"]) if plan["changed_fields"] else "none"
    return (
        f"--- {action} (changed: {changed}) ---\n"
        f"{old_text}\n"
        f"+++\n"
        f"{new_text}"
    )
