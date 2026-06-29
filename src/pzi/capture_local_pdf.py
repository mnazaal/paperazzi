"""Local PDF capture helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi import add_planning as _add_planning
from pzi.add_planning import fetch_record_for_input, merge_record_sources
from pzi.bib_repository import WritePlan, read_bib_file, serialize_bibtex
from pzi.bibtex import BibtexEntry, NormalizedRecord, bibtex_entry_to_record
from pzi.config import BibConfig
from pzi.pdf import fetch_and_store_pdf_with_fallbacks, remove_new_pdf, snapshot_pdf_paths
from pzi.pdf_download import copy_pdf_to_papers_dir
from pzi.pdf_service import extract_pdf_metadata
from pzi.protocols import (
    BinaryFetcher,
    MetadataRecordFetcher,
    S2RecordFetcher,
    SearchTranslationFetcher,
    WebTranslationFetcher,
)
from pzi.similarity import find_exact_match
from pzi.translation_server import fetch_web_translations

AddRecordResult: TypeAlias = dict[str, Any]
FetchPdf = Callable[..., tuple[str | None, str | None, str | None]]
FetchRecord = Callable[..., tuple[NormalizedRecord, list[str]]]
FetchSearch = Callable[..., list[dict[str, object]]]
CopyPdf = Callable[..., tuple[str | None, str | None]]
EnsureCitekey = Callable[..., NormalizedRecord]
AddRecord = Callable[..., AddRecordResult]


def local_pdf_base_record(
    *,
    raw_value: str,
    extracted: Mapping[str, object],
    server_url: str,
    fetch_record: FetchRecord = fetch_record_for_input,
    fetch_search: FetchSearch,
    fetch_web=fetch_web_translations,
    fetch_crossref=None,
    fetch_openalex=None,
    fetch_s2=None,
    s2_api_key: str | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
) -> NormalizedRecord:
    doi = extracted.get("doi")
    if isinstance(doi, str) and doi.strip():
        try:
            record, _provider_errors = fetch_record(
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
            return record
        except (OSError, ValueError):
            return {"doi": doi, "source_url": raw_value}

    title = extracted.get("title")
    if isinstance(title, str) and title.strip():
        try:
            results = fetch_search(title, server_url=server_url)
        except (OSError, ValueError):
            results = []
        if results:
            found_record = results[0].get("record")
            if isinstance(found_record, Mapping):
                return cast(NormalizedRecord, dict(found_record))
        return {"title": title.strip(), "source_url": raw_value}

    return {"source_url": raw_value}


def copy_local_pdf_after_citekey(
    *,
    record: NormalizedRecord,
    source_path: str,
    papers_dir: str,
    dry_run: bool,
    copy_pdf: CopyPdf | None = None,
    pdf_filename_format: str | None = None,
) -> tuple[NormalizedRecord, list[str], str | None]:
    citekey = record.get("citekey")
    if dry_run or not isinstance(citekey, str) or not citekey.strip():
        return record, [], None

    copy_pdf_fn = copy_pdf_to_papers_dir if copy_pdf is None else copy_pdf
    local_path, error = copy_pdf_fn(
        source_path=source_path,
        papers_dir=papers_dir,
        citekey=citekey,
        record=record,
        filename_format=pdf_filename_format,
    )
    if error is not None:
        return record, [error], None
    if local_path is None:
        return record, [], None

    updated = dict(record)
    updated["local_pdf_path"] = local_path
    return cast(NormalizedRecord, updated), [], local_path


def add_local_pdf(
    *,
    bib: BibConfig,
    raw_value: str,
    record_overrides: dict[str, object],
    dry_run: bool,
    server_url: str,
    fetch_search: SearchTranslationFetcher,
    ensure_citekey: EnsureCitekey,
    add_record: AddRecord,
    fetch_web: WebTranslationFetcher = fetch_web_translations,
    fetch_crossref: MetadataRecordFetcher | None = None,
    fetch_openalex: MetadataRecordFetcher | None = None,
    fetch_s2: S2RecordFetcher | None = None,
    s2_api_key: str | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    browser: str | None = None,
    browser_hook: bool = True,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
) -> AddRecordResult:
    read_result = read_bib_file(bib["path"])
    existing_records = [
        cast(NormalizedRecord, r) for r in read_result["records"]
    ]
    base_record = local_pdf_base_record(
        raw_value=raw_value,
        extracted=extract_pdf_metadata(raw_value),
        server_url=server_url,
        fetch_search=fetch_search,
        fetch_web=fetch_web,
        fetch_crossref=fetch_crossref,
        fetch_openalex=fetch_openalex,
        fetch_s2=fetch_s2,
        s2_api_key=s2_api_key,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
    )
    merged = merge_record_sources(base_record, record_overrides)
    record_with_ck = ensure_citekey(
        merged,
        existing_records,
        citekey_format=citekey_format,
    )

    existing_pdf_paths = snapshot_pdf_paths(bib["papers_dir"])
    record_with_pdf, warnings, copied_local_path = copy_local_pdf_after_citekey(
        record=record_with_ck,
        source_path=raw_value,
        papers_dir=bib["papers_dir"],
        dry_run=dry_run,
        pdf_filename_format=pdf_filename_format,
    )

    try:
        result = add_record(
            bib=bib,
            record=record_with_pdf,
            dry_run=dry_run,
            flaresolverr_url=flaresolverr_url,
            browser_pdf_cmd=browser_pdf_cmd,
            browser=browser,
            browser_hook=browser_hook,
            citekey_format=citekey_format,
            pdf_filename_format=pdf_filename_format,
        )
    except Exception:
        remove_new_pdf(copied_local_path, existing_pdf_paths)
        raise
    result["warnings"] = [*warnings, *result["warnings"]]
    return result


# ---------------------------------------------------------------------------
# PDF attachment helpers (merged from capture_pdf.py)
# ---------------------------------------------------------------------------


def attach_pdf_if_available(
    *,
    record: NormalizedRecord,
    papers_dir: str,
    dry_run: bool,
    fetch_binary: BinaryFetcher | None,
    fetch_pdf: FetchPdf | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    browser: str | None = None,
    browser_hook: bool = True,
    pdf_filename_format: str | None = None,
    api_url: str | None = None,
    api_auth_token: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    ezproxy_host: str | None = None,
) -> tuple[NormalizedRecord, list[str]]:
    pdf_url = record.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip():
        return record, []

    if record.get("local_pdf_path"):
        return record, []

    if dry_run:
        return record, []

    # When the request originated from a browser extension capture, skip
    # server-side download for non-OA sources.  The browser has an authenticated
    # session and will fetch the PDF and attach it via /attach-pdf-bytes.
    # OA sources (arXiv, preprint, DOI services, Unpaywall) are still
    # downloaded server-side because the URLs are public and reliable.
    _OA_SOURCES = frozenset({"arxiv", "preprint", "doi", "unpaywall"})
    pdf_source = record.get("pdf_source") if isinstance(record.get("pdf_source"), str) else ""
    if browser is not None and pdf_source and pdf_source not in _OA_SOURCES:
        return record, []

    citekey = record.get("citekey")
    if not isinstance(citekey, str) or not citekey.strip():
        return record, ["cannot attach PDF before citekey generation"]

    source_path = Path(pdf_url).expanduser()
    if source_path.is_file():
        local_pdf_path, error = copy_pdf_to_papers_dir(
            source_path=str(source_path),
            papers_dir=papers_dir,
            citekey=citekey,
            record=record,
            filename_format=pdf_filename_format,
        )
        if local_pdf_path is None:
            return record, [error] if error is not None else []
        updated = dict(record)
        updated["local_pdf_path"] = local_pdf_path
        return cast(NormalizedRecord, updated), []

    fetch_pdf_fn = fetch_and_store_pdf_with_fallbacks if fetch_pdf is None else fetch_pdf
    local_pdf_path, warning, error = fetch_pdf_fn(
        url=pdf_url,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
        browser=browser,
        browser_hook=browser_hook,
        record=record,
        filename_format=pdf_filename_format,
        api_url=api_url,
        api_auth_token=api_auth_token,
        desktop_fallback_hosts=desktop_fallback_hosts,
        ezproxy_host=ezproxy_host,
    )
    if local_pdf_path is None:
        return record, [error] if error is not None else []

    updated = dict(record)
    updated["local_pdf_path"] = local_pdf_path
    warnings: list[str] = []
    if warning is not None:
        warnings.append(warning)
    return cast(NormalizedRecord, updated), warnings


# ---------------------------------------------------------------------------
# Write/result helpers for capture workflows (merged from capture_write.py)
# ---------------------------------------------------------------------------


def plan_with_applied_record(
    plan: WritePlan,
    intended_record: NormalizedRecord,
    updated_entries: list[BibtexEntry],
) -> WritePlan:
    updated_records = [bibtex_entry_to_record(entry) for entry in updated_entries]

    # When force_new was used, both the old and new entries share the same
    # DOI — find_exact_match would return the old entry by identity.
    # Match by citekey instead to preserve the force-generated citekey.
    if plan.get("force_new"):
        planned_citekey = plan["record"].get("citekey")
        if isinstance(planned_citekey, str) and planned_citekey.strip():
            for idx, record in enumerate(updated_records):
                if record.get("citekey") == planned_citekey:
                    if planned_citekey == plan["record"].get("citekey"):
                        return plan
                    updated_plan = dict(plan)
                    updated_plan["record"] = record
                    updated_plan["entry"] = updated_entries[idx]
                    return cast(WritePlan, updated_plan)
        return plan

    match_index = find_exact_match(intended_record, updated_records)
    if match_index is None:
        return plan
    applied_record = updated_records[match_index]
    if applied_record.get("citekey") == plan["record"].get("citekey"):
        return plan
    updated_plan = dict(plan)
    updated_plan["record"] = applied_record
    updated_plan["entry"] = updated_entries[match_index]
    updated_plan["action"] = "update" if plan["action"] == "update" else plan["action"]
    return cast(WritePlan, updated_plan)


def build_add_record_result(
    *,
    bib: BibConfig,
    plan: WritePlan,
    warnings: list[str],
    dry_run: bool,
) -> AddRecordResult:
    citekey = plan["record"].get("citekey")
    pdf_path = plan["record"].get("local_pdf_path")
    pdf_url = plan["record"].get("pdf_url")
    pdf_fields = (
        _add_planning.pdf_result_fields(
            pdf_url=pdf_url if isinstance(pdf_url, str) else None,
            pdf_path=pdf_path if isinstance(pdf_path, str) else None,
            warnings=warnings,
            dry_run=dry_run,
        )
        if isinstance(pdf_url, str) or warnings
        else {}
    )
    prefix = "would " if dry_run else ""
    if plan["action"] == "update" and not plan.get("changed_fields", []):
        message = f"{prefix}entry unchanged (already captured)"
    else:
        message = f"{prefix}{plan['action']} entry"
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "bib_path": bib["path"],
        "action": plan["action"],
        "citekey": citekey if isinstance(citekey, str) else None,
        "pdf_path": pdf_path if isinstance(pdf_path, str) else None,
        **pdf_fields,
        "changed_fields": plan["changed_fields"],
        "dry_run": dry_run,
        "message": message,
        "warnings": warnings,
        "errors": [],
    }


def dry_run_diff(
    *,
    plan: WritePlan,
    existing_entries: list[BibtexEntry],
) -> str:
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
