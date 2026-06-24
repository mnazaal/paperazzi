"""Add/capture workflow orchestration.

Public API:
    add_input_to_bib  — main entry point for CLI / HTTP capture
    add_record_to_bib — capture a pre-built record

Metadata fetching is delegated to add_planning.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi import add_planning as _add_planning
from pzi.add_planning import (
    _fallback_record_for_input,
    fetch_record_for_input,
    has_minimum_metadata,
    merge_record_sources,
    metadata_result_confidence_warnings,
    metadata_result_diagnostics,
    minimum_metadata_diagnostics,
)
from pzi.bib_repository import (
    execute_write_plan,
    plan_bib_write,
    preview_write_plan,
    read_bib_file,
)
from pzi.bibtex import (
    NormalizedRecord,
    generate_citekey,
    normalize_authors,
    resolve_citekey_collision,
)
from pzi.capture_context import build_capture_context
from pzi.capture_local_pdf import (
    add_local_pdf,
    attach_pdf_if_available,
    build_add_record_result,
    plan_with_applied_record,
)
from pzi.config import BibConfig, load_and_resolve_bib
from pzi.format_templates import format_citekey
from pzi.identifiers import classify_input
from pzi.pdf import remove_new_pdf as _remove_new_pdf
from pzi.pdf import snapshot_pdf_paths as _snapshot_pdf_paths
from pzi.pdf_discovery import DEFAULT_DISCOVERY_STEPS, apply_pdf_discovery
from pzi.pdf_planning import is_pdf_bytes, plan_pdf_path
from pzi.similarity import build_identity_index, find_exact_match
from pzi.translation_server import (
    fetch_search_translations,
    fetch_web_translations,
)

AddRecordResult: TypeAlias = dict[str, Any]
CaptureContext: TypeAlias = dict[str, Any]

_error_result = _add_planning.error_result
_manual_record_from_overrides = _add_planning.manual_record_from_overrides
_merge_fetched_record_with_overrides = _add_planning.merge_fetched_record_with_overrides
_pdf_result_fields = _add_planning.pdf_result_fields



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
    browser: str | None = None,
    cookies: str | None = None,
    force_new: bool = False,
) -> AddRecordResult:
    context, context_error = _resolve_capture_context(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        dry_run=dry_run,
        browser_pdf_cmd_override=browser_pdf_cmd,
        browser=browser,
    )
    if context_error is not None:
        return context_error
    assert context is not None

    config = context["config"]
    bib = context["bib"]
    contact_email = context.get("contact_email")
    unpaywall_email = context["unpaywall_email"]
    s2_api_key = context["s2_api_key"]
    effective_browser_pdf_cmd = context["browser_pdf_cmd"]
    effective_browser = context.get("browser")
    citekey_format = context["citekey_format"]
    pdf_filename_format = context["pdf_filename_format"]
    api_url = context.get("api_url")
    api_auth_token = context.get("api_auth_token")
    ezproxy_host = context.get("ezproxy_host")
    desktop_fallback_hosts = context.get("desktop_fallback_hosts")
    metadata_confidence_min_score = int(config.get("metadata_confidence_min_score", 0))

    classified = classify_input(value)
    metadata_diagnostics: list[str] = []
    metadata_warnings: list[str] = []
    fallback_for_diagnostics = _fallback_record_for_input(
        kind=cast(str, classified["kind"]),
        normalized=cast(str | None, classified["normalized"]),
        raw_value=value,
    )

    def _fetch_search_with_diagnostics(query: str, *, server_url: str):
        nonlocal metadata_diagnostics, metadata_warnings
        results = fetch_search(query, server_url=server_url)
        metadata_warnings = metadata_result_confidence_warnings(
            cast(list[Mapping[str, Any]], results),
            fallback_for_diagnostics,
            min_score=metadata_confidence_min_score,
        )
        if len(results) > 1:
            metadata_diagnostics = metadata_result_diagnostics(
                cast(list[Mapping[str, Any]], results), fallback_for_diagnostics
            )
        return results

    def _fetch_web_with_diagnostics(url: str, *, server_url: str, cookies: str | None = None):
        nonlocal metadata_diagnostics, metadata_warnings
        if cookies is None:
            results = fetch_web(url, server_url=server_url)
        else:
            results = fetch_web(url, server_url=server_url, cookies=cookies)
        metadata_warnings = metadata_result_confidence_warnings(
            cast(list[Mapping[str, Any]], results),
            fallback_for_diagnostics,
            min_score=metadata_confidence_min_score,
        )
        if len(results) > 1:
            metadata_diagnostics = metadata_result_diagnostics(
                cast(list[Mapping[str, Any]], results), fallback_for_diagnostics
            )
        return results

    def _add(record: Mapping[str, object]) -> AddRecordResult:
        return add_record_with_bib(
            bib=bib,
            record=record,
            dry_run=dry_run,
            fetch_binary=fetch_binary,
            flaresolverr_url=config.get("flaresolverr_url"),
            browser_pdf_cmd=effective_browser_pdf_cmd,
            browser=effective_browser,
            browser_hook=config.get("browser_hook", True),
            citekey_format=citekey_format,
            pdf_filename_format=pdf_filename_format,
            force_new=force_new,
            file_path_style=config.get("pdf_file_path_style", "absolute"),
            api_url=api_url,
            api_auth_token=api_auth_token,
            desktop_fallback_hosts=config.get("desktop_fallback_hosts"),
            ezproxy_host=ezproxy_host,
        )

    def _finalize(result: AddRecordResult) -> AddRecordResult:
        if metadata_diagnostics:
            result["metadata_diagnostics"] = metadata_diagnostics
        if metadata_warnings:
            result["warnings"] = [*result.get("warnings", []), *metadata_warnings]
        return result

    if classified["kind"] == "local_pdf":
        return add_local_pdf(
            bib=bib,
            raw_value=value,
            record_overrides=record_overrides,
            dry_run=dry_run,
            server_url=config["translation_server_url"],
            fetch_search=fetch_search,
            ensure_citekey=ensure_citekey_for_write,
            add_record=add_record_with_bib,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_s2=fetch_s2,
            s2_api_key=s2_api_key,
            fetch_web=fetch_web,
            flaresolverr_url=config.get("flaresolverr_url"),
            browser_pdf_cmd=effective_browser_pdf_cmd,
            browser=effective_browser,
            browser_hook=config.get("browser_hook", True),
            citekey_format=citekey_format,
            pdf_filename_format=pdf_filename_format,
        )

    try:
        fetched_record = fetch_record_for_input(
            raw_value=value,
            classified=classified,
            server_url=config["translation_server_url"],
            fetch_web=_fetch_web_with_diagnostics,
            fetch_search=_fetch_search_with_diagnostics,
            contact_email=contact_email,
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
            cookies=cookies,
            api_url=api_url,
            api_auth_token=api_auth_token,
            desktop_fallback_hosts=desktop_fallback_hosts,
            pdf_discovery_parallel=config.get("pdf_discovery_parallel", False),
        )
    except Exception as exc:
        manual_record = _manual_record_from_overrides(record_overrides)
        if classified["kind"] in {"doi", "url", "pdf_url"} and has_minimum_metadata(manual_record):
            fallback_record = merge_record_sources(
                _fallback_record_for_input(
                    kind=cast(str, classified["kind"]),
                    normalized=cast(str | None, classified["normalized"]),
                    raw_value=value,
                ),
                manual_record,
            )
            fallback_record = apply_pdf_discovery(
                fallback_record,
                [
                    step for step in DEFAULT_DISCOVERY_STEPS
                    if step.__name__ != "web_attachment_step"
                ],
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
            return _finalize(_add(fallback_record))
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
                f"run 'pzi server' (it starts the translation-server) and wait for it "
                f"to be ready (expected port {host_port})."
            )
        else:
            err_msg = str(exc)
        fallback_warnings = minimum_metadata_diagnostics(manual_record)
        return _error_result(
            message="translation server error",
            errors=[err_msg],
            dry_run=dry_run,
            warnings=fallback_warnings,
        )

    merged_record = _merge_fetched_record_with_overrides(fetched_record, record_overrides)
    return _finalize(_add(merged_record))


def add_record_to_bib(
    *,
    config_path: str,
    home_dir: str,
    record: dict[str, object],
    bib_selector: str | None,
    dry_run: bool,
    force_new: bool = False,
) -> AddRecordResult:
    resolved = load_and_resolve_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        ambiguous = resolved == [_AMBIGUOUS_BIB_ERROR]
        return _error_result(
            message="could not resolve target bib" if ambiguous else "failed to load config",
            errors=["no matching bib found or selection is ambiguous"] if ambiguous else resolved,
            dry_run=dry_run,
            warnings=[],
        )
    config, bib = resolved

    return add_record_with_bib(
        bib=bib,
        record=record,
        dry_run=dry_run,
        browser_hook=config.get("browser_hook", True),
        citekey_format=config.get("citekey_format"),
        pdf_filename_format=config.get("pdf_filename_format"),
        force_new=force_new,
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )


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
    browser: str | None = None,
) -> tuple[CaptureContext | None, AddRecordResult | None]:
    """Load config, select the bib, and resolve runtime-only capture options."""
    resolved = load_and_resolve_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        ambiguous = resolved == [_AMBIGUOUS_BIB_ERROR]
        return None, _error_result(
            message="could not resolve target bib" if ambiguous else "failed to load config",
            errors=["no matching bib found"] if ambiguous else resolved,
            dry_run=dry_run,
            warnings=[],
        )
    config, bib = resolved

    try:
        context = build_capture_context(
            config=config,
            bib=bib,
            browser_pdf_cmd_override=browser_pdf_cmd_override,
            browser=browser,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return None, _error_result(
            message="failed to resolve capture context",
            errors=[str(exc)],
            dry_run=dry_run,
            warnings=[],
        )
    return context, None


_AMBIGUOUS_BIB_ERROR = "no matching library target found or selection is ambiguous"


def add_record_with_bib(
    *,
    bib: BibConfig,
    record: Mapping[str, object],
    dry_run: bool,
    fetch_binary=None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    browser: str | None = None,
    browser_hook: bool = True,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
    force_new: bool = False,
    api_url: str | None = None,
    api_auth_token: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    ezproxy_host: str | None = None,
    file_path_style: str = "absolute",
) -> AddRecordResult:
    read_result = read_bib_file(bib["path"])
    typed_existing_records = [
        cast(NormalizedRecord, existing) for existing in read_result["records"]
    ]
    # Build the identity index once and reuse it across the several exact-match
    # lookups this write performs, instead of rebuilding it per call.
    existing_index = build_identity_index(typed_existing_records)
    typed_record = ensure_citekey_for_write(
        cast(NormalizedRecord, dict(record)),
        typed_existing_records,
        citekey_format=citekey_format,
        force_new=force_new,
        index=existing_index,
    )
    typed_record = reuse_existing_pdf_fields_for_exact_match(
        typed_record,
        typed_existing_records,
        force_new=force_new,
        index=existing_index,
    )
    typed_record = reuse_orphan_pdf_for_planned_path(
        typed_record,
        papers_dir=bib["papers_dir"],
        pdf_filename_format=pdf_filename_format,
    )
    existing_pdf_paths = _snapshot_pdf_paths(bib["papers_dir"])
    record_with_pdf, warnings = attach_pdf_if_available(
        record=typed_record,
        papers_dir=bib["papers_dir"],
        dry_run=dry_run,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
        browser=browser,
        browser_hook=browser_hook,
        pdf_filename_format=pdf_filename_format,
        api_url=api_url,
        api_auth_token=api_auth_token,
        desktop_fallback_hosts=desktop_fallback_hosts,
        ezproxy_host=ezproxy_host,
    )
    record_with_hint = _add_planning.attach_similarity_hint(
        record_with_pdf, typed_existing_records, index=existing_index,
    )
    plan = plan_bib_write(
        record_with_hint, typed_existing_records, force_new=force_new, index=existing_index,
    )

    if not dry_run:
        try:
            updated_entries = execute_write_plan(
                bib["path"], plan, file_path_style=file_path_style
            )
            plan = plan_with_applied_record(plan, record_with_hint, updated_entries)
        except Exception:
            pdf_path_for_cleanup = record_with_hint.get("local_pdf_path")
            _remove_new_pdf(
                pdf_path_for_cleanup if isinstance(pdf_path_for_cleanup, str) else None,
                existing_pdf_paths,
            )
            raise

    result: AddRecordResult = build_add_record_result(
        bib=bib,
        plan=plan,
        warnings=warnings,
        dry_run=dry_run,
    )

    if dry_run:
        result["diff"] = preview_write_plan(bib["path"], plan)["diff"]

    return result


# ---------------------------------------------------------------------------
# Identity and existing-PDF reuse helpers (merged from capture_identity.py)
# ---------------------------------------------------------------------------


def ensure_citekey_for_write(
    record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    citekey_format: str | None = None,
    force_new: bool = False,
    index: dict | None = None,
) -> NormalizedRecord:
    match_index = find_exact_match(record, existing_records, index=index)
    if match_index is not None:
        existing_citekey = existing_records[match_index].get("citekey")
        if isinstance(existing_citekey, str) and existing_citekey.strip():
            if not force_new:
                matched = dict(record)
                matched["citekey"] = existing_citekey
                return cast(NormalizedRecord, matched)
            # force_new: create a duplicate citekey with a numeric suffix (-2, -3, …).
            existing_keys = existing_citekeys(existing_records)
            resolved = resolve_citekey_collision(existing_citekey.strip(), existing_keys)
            updated = dict(record)
            updated["citekey"] = resolved
            return cast(NormalizedRecord, updated)

    ck = record.get("citekey")
    if isinstance(ck, str) and bool(ck.strip()):
        existing_keys = existing_citekeys(existing_records)
        resolved = resolve_citekey_collision(ck.strip(), existing_keys)
        if resolved == ck.strip():
            return record
        updated = dict(record)
        updated["citekey"] = resolved
        return cast(NormalizedRecord, updated)

    generated = dict(record)
    existing_keys = existing_citekeys(existing_records)
    if citekey_format:
        generated["citekey"] = format_citekey(citekey_format, record, existing_keys)
    else:
        generated["citekey"] = generate_citekey(
            {
                "authors": normalize_authors(record.get("authors")),
                "title": cast(str | None, record.get("title")),
                "year": cast(int | None, record.get("year")),
            },
            existing_keys,
        )
    return cast(NormalizedRecord, generated)


def existing_citekeys(existing_records: list[NormalizedRecord]) -> set[str]:
    return {
        citekey
        for existing in existing_records
        for citekey in [existing.get("citekey")]
        if isinstance(citekey, str) and citekey.strip()
    }


def reuse_existing_pdf_fields_for_exact_match(
    record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    force_new: bool = False,
    index: dict | None = None,
) -> NormalizedRecord:
    if record.get("local_pdf_path"):
        return record
    # When force_new is set, the new entry should not share the existing
    # entry's PDF — it needs its own.
    if force_new:
        return record
    match_index = find_exact_match(record, existing_records, index=index)
    if match_index is None:
        return record

    existing = existing_records[match_index]
    local_pdf_path = existing.get("local_pdf_path")
    if not isinstance(local_pdf_path, str) or not local_pdf_path.strip():
        return record

    updated = dict(record)
    updated["local_pdf_path"] = local_pdf_path
    existing_pdf_url = existing.get("pdf_url")
    if isinstance(existing_pdf_url, str) and existing_pdf_url.strip():
        updated["pdf_url"] = existing_pdf_url
    return cast(NormalizedRecord, updated)


def reuse_orphan_pdf_for_planned_path(
    record: NormalizedRecord,
    *,
    papers_dir: str,
    pdf_filename_format: str | None = None,
) -> NormalizedRecord:
    if record.get("local_pdf_path"):
        return record
    if not record.get("pdf_url"):
        return record
    citekey = record.get("citekey")
    if not isinstance(citekey, str) or not citekey.strip():
        return record

    planned = plan_pdf_path(
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=pdf_filename_format,
    )
    try:
        data = Path(planned).read_bytes()
    except OSError:
        return record
    if not is_pdf_bytes(data):
        return record

    updated = dict(record)
    updated["local_pdf_path"] = planned
    return cast(NormalizedRecord, updated)
