"""Add/capture workflow orchestration.

Public API:
    add_input_to_bib  — main entry point for CLI / HTTP capture
    add_record_to_bib — capture a pre-built record

Metadata fetching is delegated to add_planning.py.
"""

from __future__ import annotations

import urllib.error
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit

from pzi import add_planning as _add_planning
from pzi.add_planning import (
    _fallback_record_for_input,
    build_discovery_context,
    fetch_record_for_input,
    has_minimum_metadata,
    merge_record_sources,
    metadata_result_confidence_warnings,
    metadata_result_diagnostics,
    minimum_metadata_diagnostics,
)
from pzi.bib_repository import (
    ConcurrentEditError,
    WritePlan,
    batch_write_session,
    execute_write_plan,
    plan_bib_write,
    preview_write_plan,
    read_bib_file,
    validate_bibtex_roundtrip,
)
from pzi.bibtex import (
    NormalizedRecord,
    generate_citekey,
    normalize_authors,
    resolve_citekey_collision,
)
from pzi.capture_context import CaptureContext, build_capture_context
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
from pzi.protocols import (
    BinaryFetcher,
    HtmlFetcher,
    MetadataRecordFetcher,
    S2RecordFetcher,
    SearchTranslationFetcher,
    UnpaywallFetcher,
    WebTranslationFetcher,
)
from pzi.similarity import build_identity_index, find_exact_match
from pzi.translation_server import (
    fetch_search_translations,
    fetch_web_translations,
)

AddRecordResult: TypeAlias = dict[str, Any]

_error_result = _add_planning.error_result
_manual_record_from_overrides = _add_planning.manual_record_from_overrides
_merge_fetched_record_with_overrides = _add_planning.merge_fetched_record_with_overrides
_pdf_result_fields = _add_planning.pdf_result_fields

_AMBIGUOUS_BIB_ERROR = "no matching library target found or selection is ambiguous"


def describe_invalid_add_input(value: str) -> str | None:
    """Return why *value* is not valid ``pzi add`` input, or ``None`` if it is.

    ``pzi add`` accepts a DOI, a URL, or a local PDF path.  A bare word or typo
    (e.g. ``pzi add l``) classifies as ``unknown`` and would otherwise be
    written out as an empty ``unknown…untitled`` placeholder entry; a ``*.pdf``
    argument whose file does not exist is likewise rejected.  Callers use this
    to fail fast — before starting the translation-server or writing anything.
    """
    classified = classify_input(value)
    if classified["kind"] == "unknown":
        return f"{value!r} is not a DOI, URL, or local PDF path"
    if classified["kind"] == "local_pdf" and not Path(value).expanduser().is_file():
        return f"PDF file not found: {value}"
    return None


def add_input_to_bib(
    *,
    config_path: str,
    home_dir: str,
    value: str,
    record_overrides: dict[str, object],
    bib_selector: str | None,
    dry_run: bool,
    fetch_web: WebTranslationFetcher = fetch_web_translations,
    fetch_search: SearchTranslationFetcher = fetch_search_translations,
    fetch_binary: BinaryFetcher | None = None,
    fetch_unpaywall: UnpaywallFetcher | None = None,
    fetch_crossref: MetadataRecordFetcher | None = None,
    fetch_openalex: MetadataRecordFetcher | None = None,
    fetch_s2: S2RecordFetcher | None = None,
    fetch_flaresolverr: HtmlFetcher | None = None,
    pdf_url_candidates: list[str] | None = None,
    browser_pdf_cmd: str | None = None,
    browser: str | None = None,
    cookies: str | None = None,
    force_new: bool = False,
    metadata_strict: bool = False,
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

    config = context.config
    bib = context.bib
    contact_email = context.contact_email
    unpaywall_email = context.unpaywall_email
    s2_api_key = context.s2_api_key
    effective_browser_pdf_cmd = context.browser_pdf_cmd
    effective_browser = context.browser
    citekey_format = context.citekey_format
    pdf_filename_format = context.pdf_filename_format
    api_url = context.api_url
    api_auth_token = context.api_auth_token
    ezproxy_host = context.ezproxy_host
    desktop_fallback_hosts = context.desktop_fallback_hosts
    metadata_confidence_min_score = int(config.get("metadata_confidence_min_score", 0))

    classified = classify_input(value)
    invalid_input = describe_invalid_add_input(value)
    if invalid_input is not None:
        # Reject unrecognized input here too (not just at the CLI) so the HTTP
        # API and `--from-file` batch path never write a junk placeholder entry.
        return _error_result(
            message="invalid input", errors=[invalid_input], dry_run=dry_run, warnings=[]
        )
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
            desktop_fallback_hosts=desktop_fallback_hosts,
            ezproxy_host=ezproxy_host,
        )

    provider_errors: list[str] = []

    def _finalize(result: AddRecordResult) -> AddRecordResult:
        if metadata_diagnostics:
            result["metadata_diagnostics"] = metadata_diagnostics
        extra_warnings = [*metadata_warnings, *(
            f"provider error ({e})" for e in provider_errors
        )]
        if extra_warnings:
            result["warnings"] = [*result.get("warnings", []), *extra_warnings]
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
        fetched_record, _fetched_provider_errors = fetch_record_for_input(
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
        provider_errors.extend(_fetched_provider_errors)
        effective_strict = metadata_strict or bool(config.get("metadata_strict", False))
        if effective_strict and provider_errors:
            return _error_result(
                message="metadata provider error (--strict-metadata)",
                errors=list(provider_errors),
                dry_run=dry_run,
                warnings=[],
            )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Narrowly scoped to what the translation-server / metadata-provider
        # fetchers actually raise for network and malformed-response
        # failures, so a genuine bug elsewhere in this block (KeyError,
        # AttributeError, TypeError, ...) surfaces as a crash instead of
        # being silently misreported as "translation server error".
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
                # The translation server is unreachable here, so skip the step
                # that depends on its /web attachments; everything else still
                # runs against the shared discovery context.
                [
                    step for step in DEFAULT_DISCOVERY_STEPS
                    if step.__name__ != "web_attachment_step"
                ],
                build_discovery_context(
                    raw_value=value,
                    server_url=config["translation_server_url"],
                    unpaywall_email=unpaywall_email,
                    contact_email=contact_email,
                    s2_api_key=s2_api_key,
                    flaresolverr_url=config.get("flaresolverr_url"),
                    browser_pdf_cmd=effective_browser_pdf_cmd,
                    pdf_url_candidates=pdf_url_candidates,
                    cookies=cookies,
                    fetch_web=fetch_web,
                    fetch_unpaywall=fetch_unpaywall,
                    fetch_crossref=fetch_crossref,
                    fetch_openalex=fetch_openalex,
                    fetch_s2=fetch_s2,
                    fetch_flaresolverr=fetch_flaresolverr,
                    api_url=api_url,
                    api_auth_token=api_auth_token,
                    desktop_fallback_hosts=desktop_fallback_hosts,
                ),
            )
            return _finalize(_add(fallback_record))
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


def add_record_with_bib(
    *,
    bib: BibConfig,
    record: Mapping[str, object],
    dry_run: bool,
    fetch_binary: BinaryFetcher | None = None,
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
    # The PDF download happens exactly once, before planning. The plan+write
    # tail below may run twice (concurrent-edit retry), but never re-downloads:
    # ``record_with_pdf`` already carries ``local_pdf_path``, so the reuse
    # helpers short-circuit on the second pass.
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

    def _build_plan(
        existing_records: list[NormalizedRecord],
    ) -> tuple[NormalizedRecord, WritePlan]:
        """Derive the citekey/dedup/PDF-reuse/hint plan for ``record_with_pdf``
        against a snapshot of ``existing_records``. Pure apart from the orphan
        PDF probe; safe to call repeatedly (no network, no re-download)."""
        index = build_identity_index(existing_records)
        rec = ensure_citekey_for_write(
            record_with_pdf, existing_records,
            citekey_format=citekey_format, force_new=force_new, index=index,
        )
        rec = reuse_existing_pdf_fields_for_exact_match(
            rec, existing_records, force_new=force_new, index=index,
        )
        rec = reuse_orphan_pdf_for_planned_path(
            rec, papers_dir=bib["papers_dir"], pdf_filename_format=pdf_filename_format,
        )
        rec = _add_planning.attach_similarity_hint(rec, existing_records, index=index)
        return rec, plan_bib_write(rec, existing_records, force_new=force_new, index=index)

    record_with_hint, plan = _build_plan(typed_existing_records)

    if not dry_run:
        record_with_hint, plan = _execute_plan_with_retry(
            bib=bib,
            build_plan=_build_plan,
            initial_records=typed_existing_records,
            initial_plan=(record_with_hint, plan),
            existing_pdf_paths=existing_pdf_paths,
            file_path_style=file_path_style,
        )

    result: AddRecordResult = build_add_record_result(
        bib=bib,
        plan=plan,
        warnings=warnings,
        dry_run=dry_run,
    )

    if dry_run:
        result["diff"] = preview_write_plan(bib["path"], plan)["diff"]

    return result


def _execute_plan_with_retry(
    *,
    bib: BibConfig,
    build_plan: Callable[[list[NormalizedRecord]], tuple[NormalizedRecord, WritePlan]],
    initial_records: list[NormalizedRecord],
    initial_plan: tuple[NormalizedRecord, WritePlan],
    existing_pdf_paths: set[Path],
    file_path_style: str,
) -> tuple[NormalizedRecord, WritePlan]:
    """Commit a write plan, retrying once on a concurrent external edit.

    ``execute_write_plan`` raises :class:`ConcurrentEditError` *before* writing
    when the bib changed between snapshot and lock; the race window is tiny, so
    a single retry — re-reading the library and rebuilding the plan against the
    now-current records — almost always succeeds. The already-downloaded PDF is
    preserved across the retry and only cleaned up when we give up (final
    failure) or any other exception aborts the write.
    """
    record_with_hint, plan = initial_plan
    records = initial_records
    for attempt in range(2):
        try:
            updated_entries = execute_write_plan(
                bib["path"], plan, file_path_style=file_path_style
            )
            return record_with_hint, plan_with_applied_record(
                plan, record_with_hint, updated_entries
            )
        except ConcurrentEditError:
            if attempt == 1:
                _cleanup_new_pdf(record_with_hint, existing_pdf_paths)
                raise
            # Re-read the externally-edited library and replan against it.
            records = [
                cast(NormalizedRecord, existing)
                for existing in read_bib_file(bib["path"])["records"]
            ]
            record_with_hint, plan = build_plan(records)
        except Exception:
            _cleanup_new_pdf(record_with_hint, existing_pdf_paths)
            raise
    # Unreachable: the loop either returns, retries, or raises.
    raise AssertionError("retry loop exited without committing or raising")


def _cleanup_new_pdf(
    record_with_hint: NormalizedRecord, existing_pdf_paths: set[Path]
) -> None:
    pdf_path_for_cleanup = record_with_hint.get("local_pdf_path")
    _remove_new_pdf(
        pdf_path_for_cleanup if isinstance(pdf_path_for_cleanup, str) else None,
        existing_pdf_paths,
    )


def add_records_to_bib_batch(
    *,
    bib: BibConfig,
    records: Sequence[Mapping[str, object]],
    dry_run: bool,
    force_new: bool = False,
    browser_hook: bool = True,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
    file_path_style: str = "absolute",
    fetch_binary: BinaryFetcher | None = None,
) -> list[AddRecordResult]:
    """Plan and write many records into one bib under a single lock and write.

    Per-record semantics match :func:`add_record_with_bib` — citekey generation
    and collision resolution, identity dedup/merge, PDF reuse/attach, similarity
    hints — but the library is parsed and serialized once instead of once per
    record.  State (the records list and identity index) accumulates in memory
    so that record *K* dedups and resolves citekeys against the library plus
    records 1…K-1, exactly as the repeated single-write path does.

    Returns one :data:`AddRecordResult` per input record, in order.  A single
    exclusive lock is held for the whole batch (including any rare per-record
    PDF downloads), so the import is all-or-nothing rather than committed
    per entry.  Bad records are skipped with an error result; good ones still
    write.  This is the bulk path behind :func:`pzi.import_service`.
    """
    papers_dir = bib["papers_dir"]
    existing_pdf_paths = _snapshot_pdf_paths(papers_dir)
    results: list[AddRecordResult] = []
    # PDFs downloaded for records that have been applied to the session.
    # If the batch fails at commit time (check_consistency or roundtrip
    # validation), the bib is not written but these files exist on disk —
    # the outer except removes them to avoid orphans.
    batch_pdfs: list[str] = []

    try:
        with batch_write_session(
            bib["path"], file_path_style=file_path_style, write=not dry_run,
        ) as session:
            # ``session`` owns entries/records/index and keeps them in lockstep;
            # the local aliases just make the per-record read calls below terse.
            existing_records = session.records
            index = session.index

            for raw in records:
                record_pdf: str | None = None
                try:
                    typed = ensure_citekey_for_write(
                        cast(NormalizedRecord, dict(raw)), existing_records,
                        citekey_format=citekey_format, force_new=force_new, index=index,
                    )
                    typed = reuse_existing_pdf_fields_for_exact_match(
                        typed, existing_records, force_new=force_new, index=index,
                    )
                    typed = reuse_orphan_pdf_for_planned_path(
                        typed, papers_dir=papers_dir, pdf_filename_format=pdf_filename_format,
                    )
                    typed, warnings = attach_pdf_if_available(
                        record=typed, papers_dir=papers_dir, dry_run=dry_run,
                        fetch_binary=fetch_binary, browser_hook=browser_hook,
                        pdf_filename_format=pdf_filename_format,
                    )
                    pdf_path = typed.get("local_pdf_path")
                    record_pdf = pdf_path if isinstance(pdf_path, str) else None
                    typed = _add_planning.attach_similarity_hint(
                        typed, existing_records, index=index,
                    )
                    plan = plan_bib_write(
                        typed, existing_records, force_new=force_new, index=index,
                    )
                    validate_bibtex_roundtrip([plan["entry"]])
                except Exception as exc:
                    if not dry_run:
                        _remove_new_pdf(record_pdf, existing_pdf_paths)
                    results.append(_error_result(
                        message="failed to import record", errors=[str(exc)],
                        dry_run=dry_run, warnings=[], bib=bib,
                    ))
                    continue

                if not dry_run:
                    # Register before apply_plan: if apply_plan raises an
                    # internal invariant error, or if the commit-time checks
                    # (check_consistency / roundtrip) raise after the loop, the
                    # outer handler removes this PDF. On a clean commit the
                    # outer except is not triggered, so no spurious cleanup.
                    if record_pdf is not None:
                        batch_pdfs.append(record_pdf)
                    session.apply_plan(plan)
                results.append(build_add_record_result(
                    bib=bib, plan=plan, warnings=warnings, dry_run=dry_run,
                ))

    except Exception:
        # Batch commit failed (invariant violation or roundtrip check). The bib
        # was not written. Remove any PDFs we downloaded for applied records so
        # nothing is left orphaned on disk.
        if not dry_run:
            for pdf_path in batch_pdfs:
                _remove_new_pdf(pdf_path, existing_pdf_paths)
        raise

    return results


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
