"""PDF retry, attach, and metadata extraction services."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi.add_planning import next_pdf_candidate_for_config
from pzi.bib_repository import find_entry_index, read_bib_file, update_bib_entry
from pzi.bibtex import (
    BibtexEntry,
    NormalizedRecord,
    apply_record_to_entry,
)
from pzi.capture_context import resolve_api_auth_token
from pzi.config import (
    DEFAULT_API_LISTEN_HOST,
    DEFAULT_API_LISTEN_PORT,
    AppConfig,
    BibResolutionFailure,
    load_bib_target,
)
from pzi.errors import REASON_CONFIG, REASON_NOT_FOUND, REASON_UNAVAILABLE, REASON_USAGE
from pzi.pdf import (
    fetch_and_store_pdf_trying_sources,
    fetch_and_store_pdf_with_fallbacks,
    write_pdf_bytes,
)
from pzi.pdf import remove_new_pdf as _remove_new_pdf
from pzi.pdf import snapshot_pdf_paths as _snapshot_pdf_paths
from pzi.pdf_discovery import discover_pdf_url_for_record
from pzi.pdf_download import copy_pdf_to_papers_dir
from pzi.pdf_planning import pdf_file_present
from pzi.protocols import BinaryFetcher


def _update_entry_keeping_pdf_consistent(
    update: Callable[[], Any],
    *,
    new_pdf: str | None,
    existing_pdf_paths: set[Path],
) -> Any:
    """Run *update*, removing a newly downloaded PDF if the write raises.

    Every PDF path here cleaned up when the entry had *disappeared* — the one
    outcome `update_bib_entry` reports by returning — and not when it raised.
    So a refused write (a duplicate citekey elsewhere in the file, a library
    that no longer round-trips, a full disk) left the downloaded file on disk
    with nothing referring to it, and a later `library clean --fix` quarantined it:
    a second command tidying up after the first.

    Only files this operation created are removed; *existing_pdf_paths* is the
    snapshot taken before the download, so a pre-existing PDF is never touched.
    `capture_local_pdf` already does exactly this around its own write.
    """
    try:
        return update()
    except BaseException:
        _remove_new_pdf(new_pdf, existing_pdf_paths)
        raise


def _superseded_pdf_warning(
    record: Mapping[str, Any] | None, new_path: str | None
) -> str | None:
    """Warn when a re-attach leaves the previous PDF orphaned.

    `resolve_pdf_destination` never overwrites — it suffixes `-1`, `-2` — which
    is right, but nothing said so. Re-attaching left the old file on disk with
    no entry pointing at it, and the user was told only that a PDF was
    attached. Reported rather than deleted: the old file may be the better
    scan, and silently removing a user's PDF is worse than leaving one behind.
    """
    if not isinstance(record, Mapping) or not new_path:
        return None
    previous = record.get("local_pdf_path")
    if not isinstance(previous, str) or not previous.strip():
        return None
    if os.path.abspath(previous) == os.path.abspath(new_path):
        return None
    if not os.path.exists(previous):
        return None
    return (
        f"previous PDF superseded and left on disk: {previous} "
        f"(the entry now points at {new_path})"
    )


def _fallback_kwargs(config: AppConfig) -> dict[str, Any]:
    """Fallback-chain knobs, read the same way `pzi add` reads them.

    `pdf retry` and `pdf attach` used to call the direct-only downloader while
    telling the user, on failure, to "configure browser_pdf_cmd" -- machinery
    that code path never invoked. They now run the same chain `add` does:
    direct, then the server browser, then the browser_pdf_cmd hook, then
    FlareSolverr, then the desktop-download watcher.

    Reads the keys directly rather than going through `build_capture_context`,
    which additionally resolves contact/unpaywall/S2 credentials that play no
    part in fetching a PDF.
    """
    api_url = config.get("api_url")
    if not api_url:
        api_url = (
            f"http://{config.get('api_listen_host', DEFAULT_API_LISTEN_HOST)}"
            f":{config.get('api_listen_port', DEFAULT_API_LISTEN_PORT)}"
        )
    return {
        "flaresolverr_url": config.get("flaresolverr_url"),
        "browser_pdf_cmd": config.get("browser_pdf_cmd"),
        "browser_hook": config.get("browser_hook", True),
        "api_url": api_url,
        "api_auth_token": resolve_api_auth_token(config),
        "desktop_fallback_hosts": set(config.get("desktop_fallback_hosts") or []),
        "ezproxy_host": config.get("ezproxy_host"),
    }


PdfRetryResult: TypeAlias = dict[str, Any]



PdfAttachResult: TypeAlias = dict[str, Any]



PdfAttachBytesResult: TypeAlias = dict[str, Any]

PdfExtractionResult: TypeAlias = dict[str, Any]

_PDF_BROWSER_RETRY_HINT = "hint: open the actual PDF tab in your browser and click pzi again"


def _pdf_failure_errors(error: str | None, fallback: str) -> list[str]:
    """Return PDF failure errors with actionable browser-session recovery hint."""
    base = [error] if error else [fallback]
    return [*base, _PDF_BROWSER_RETRY_HINT]



def retry_pdf(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    fetch_binary: BinaryFetcher | None = None,
    deep: bool = False,
) -> PdfRetryResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "could not resolve target bib",
            "reason": REASON_CONFIG,
            "warnings": [],
            "errors": resolved.errors,
        }
    config, bib = resolved
    discovered_from: str | None = None

    read_result = read_bib_file(bib["path"])
    entries = read_result["entries"]
    index = find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "citekey not found",
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    record = _record_at(read_result, index)
    pdf_url = record.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url:
        # Derive one rather than refuse. `retry` used to demand a *stored*
        # `pdf_url` and did no discovery at all, so an entry carrying `eprint`
        # or a preprint URL — everything needed to build the link — was turned
        # away with "no PDF URL on entry" and the user pasted the URL by hand.
        # Pure steps only by default: they are arithmetic on identifiers the
        # entry already has, so this costs no network call and cannot rate-limit.
        # `--discover` adds the HTTP steps (Unpaywall, DOI resolution, landing
        # pages), which are worth a flag because a `--failed-only` sweep of them
        # is long and provider-throttled.
        record = discover_pdf_url_for_record(
            cast(NormalizedRecord, record), config=config, deep=deep,
        )
        pdf_url = record.get("pdf_url")
        if isinstance(pdf_url, str) and pdf_url:
            source = record.get("pdf_source")
            # Say so: a URL that was *derived* rather than read off the entry is
            # a different claim, and naming the step is what makes a wrong guess
            # diagnosable instead of mysterious.
            discovered_from = (
                f"derived PDF URL from {source}: {pdf_url}"
                if isinstance(source, str) and source
                else f"derived PDF URL: {pdf_url}"
            )

    if not isinstance(pdf_url, str) or not pdf_url:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "no PDF URL on entry",
            # Same shape as "citekey not found" a few lines up in this
            # function: the thing retry_pdf needs (a URL to retry) is absent
            # from the record, not merely unreachable.
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": ["no PDF URL found on entry"],
        }

    # Both arguments passed unconditionally: pdf_planning already handles a
    # falsy filename_format and a None record, so branching on them here only
    # duplicated that decision.
    filename_format = config.get("pdf_filename_format")
    existing_pdf_paths = _snapshot_pdf_paths(bib["papers_dir"])
    outcome = fetch_and_store_pdf_trying_sources(
        url=pdf_url,
        record=cast(NormalizedRecord, record),
        next_candidate=next_pdf_candidate_for_config(config, bib),
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        fetch_binary=fetch_binary,
        filename_format=filename_format,
        **_fallback_kwargs(config),
    )
    discovery_notes = [discovered_from] if discovered_from else []
    local_pdf_path = outcome.local_pdf_path
    # The URL that produced the file, which is not necessarily the stored one:
    # the whole point of the fallback is that a different source may have
    # answered, and writing the dead URL back would misreport it.
    pdf_url = str(outcome.record.get("pdf_url") or pdf_url)
    warning = ("; ".join(outcome.errors) or None) if local_pdf_path is None else outcome.warning
    if local_pdf_path is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "failed to fetch PDF",
            # A URL was found; the fetch itself (network, source, or
            # FlareSolverr) failed. That is an external dependency being
            # unavailable, not a bad request or missing resource — the same
            # class of failure as `discover_via_server_api`'s "not reachable".
            "reason": REASON_UNAVAILABLE,
            "warnings": [],
            "errors": _pdf_failure_errors(warning, "failed to fetch PDF"),
        }

    update_result = _update_entry_keeping_pdf_consistent(
        lambda: update_bib_entry(
            bib["path"],
            citekey,
            lambda entry, record: _entry_with_pdf_fields(
                entry,
                cast(NormalizedRecord, dict(record)),
                local_pdf_path=local_pdf_path,
                pdf_url=pdf_url,
            ),
            file_path_style=config.get("pdf_file_path_style", "absolute"),
        ),
        new_pdf=local_pdf_path,
        existing_pdf_paths=existing_pdf_paths,
    )
    if not update_result["found"]:
        _remove_new_pdf(local_pdf_path, existing_pdf_paths)
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "message": "citekey disappeared",
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": citekey,
        "local_pdf_path": local_pdf_path,
        "message": "fetched PDF",
        # The chain's warning — notably FlareSolverr's "may violate publisher
        # terms of service" notice. `pzi add` surfaces it; hardcoding `[]` here
        # meant the same acquisition reported differently depending on which
        # command performed it.
        "warnings": [
            note
            for note in (
                *discovery_notes,
                warning,
                _superseded_pdf_warning(_record_at(read_result, index), local_pdf_path),
            )
            if note
        ],
        "errors": [],
    }


def retry_failed_pdfs(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    deep: bool = False,
) -> PdfRetryResult:
    """Retry PDF download for all entries that need it (no local PDF).

    Returns a summary result with per-entry success/failure details.

    An entry with no *stored* PDF URL is not skipped before discovery has had a
    look: the single-entry path derives one from the identifiers the entry
    already carries, and a batch that applied a stricter test would report
    "skipped, no URL" for entries `pzi pdf retry <citekey>` handles fine — the
    same fix landing at one call site and not its sibling. *deep* has the same
    meaning as there: pure steps only unless asked for the network ones.
    """
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "bib_name": None,
            "message": "could not resolve target bib",
            "reason": REASON_CONFIG,
            "errors": resolved.errors,
        }
    config, bib = resolved

    read_result = read_bib_file(bib["path"])
    entries = read_result["entries"]
    records = read_result.get("records") or []
    fallback_kwargs = _fallback_kwargs(config)
    # Built once for the whole run: it resolves credentials, which are the same
    # for every entry in the batch.
    next_candidate = next_pdf_candidate_for_config(config, bib)
    filename_format = config.get("pdf_filename_format")
    existing_pdf_paths = _snapshot_pdf_paths(bib["papers_dir"])

    # Find entries needing retry
    needs_retry: list[tuple[int, str, str]] = []  # (index, citekey, pdf_url)
    skipped_already_has_pdf = 0
    skipped_no_url = 0

    for i, entry in enumerate(entries):
        citekey = entry.get("citekey", "")
        if not isinstance(citekey, str) or not citekey:
            continue

        # Check if entry already has a local PDF
        record = records[i] if i < len(records) else {}
        local_pdf = record.get("local_pdf_path") if isinstance(record, dict) else None
        if pdf_file_present(local_pdf):
            skipped_already_has_pdf += 1
            continue

        # Check if entry has a PDF URL, deriving one when it does not.
        pdf_url = record.get("pdf_url") if isinstance(record, dict) else None
        if (not isinstance(pdf_url, str) or not pdf_url) and isinstance(record, dict):
            derived = discover_pdf_url_for_record(
                cast(NormalizedRecord, record), config=config, deep=deep,
            )
            pdf_url = derived.get("pdf_url")
        if not isinstance(pdf_url, str) or not pdf_url:
            skipped_no_url += 1
            continue

        needs_retry.append((i, citekey, pdf_url))

    if not needs_retry:
        return {
            "status": "ok",
            "bib_name": bib["name"],
            "total": 0,
            "succeeded": 0,
            "skipped_already_has_pdf": skipped_already_has_pdf,
            "skipped_no_url": skipped_no_url,
            "failures": [],
        }

    succeeded = 0
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, citekey, pdf_url in needs_retry:
        record = records[index] if index < len(records) else None
        record_dict = _record_at(read_result, index)

        outcome = fetch_and_store_pdf_trying_sources(
            url=pdf_url,
            record=cast(NormalizedRecord, record_dict),
            next_candidate=next_candidate,
            papers_dir=bib["papers_dir"],
            citekey=citekey,
            filename_format=filename_format,
            **fallback_kwargs,
        )
        local_pdf_path = outcome.local_pdf_path
        pdf_url = str(outcome.record.get("pdf_url") or pdf_url)
        warning = (
            ("; ".join(outcome.errors) or None) if local_pdf_path is None else outcome.warning
        )

        if local_pdf_path is None:
            failures.append({"citekey": citekey, "error": warning or "failed to fetch PDF"})
            continue

        update_result = _update_entry_keeping_pdf_consistent(
            lambda: update_bib_entry(
                bib["path"],
                citekey,
                lambda entry, rec: _entry_with_pdf_fields(
                    entry,
                    cast(NormalizedRecord, dict(rec)),
                    local_pdf_path=cast(str, local_pdf_path),
                    pdf_url=pdf_url,
                ),
                file_path_style=config.get("pdf_file_path_style", "absolute"),
            ),
            new_pdf=local_pdf_path,
            existing_pdf_paths=existing_pdf_paths,
        )
        if not update_result["found"]:
            _remove_new_pdf(local_pdf_path, existing_pdf_paths)
            failures.append({"citekey": citekey, "error": "citekey disappeared during update"})
            continue

        succeeded += 1
        if warning:
            warnings.append(f"{citekey}: {warning}")

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "total": len(needs_retry),
        "succeeded": succeeded,
        "skipped_already_has_pdf": skipped_already_has_pdf,
        "skipped_no_url": skipped_no_url,
        "failures": failures,
        # Per-entry chain warnings — the FlareSolverr terms-of-service notice
        # above all. `pzi add` has always surfaced these; this path dropped them.
        "warnings": warnings,
    }


def attach_pdf(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    source: str,
    fetch_binary: BinaryFetcher | None = None,
) -> PdfAttachResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "could not resolve target bib",
            "reason": REASON_CONFIG,
            "warnings": [],
            "errors": resolved.errors,
        }
    config, bib = resolved

    read_result = read_bib_file(bib["path"])
    entries = read_result["entries"]
    index = find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "citekey not found",
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    filename_format = config.get("pdf_filename_format")
    existing_pdf_paths = _snapshot_pdf_paths(bib["papers_dir"])
    # `_store_pdf_source` returns `error or warning`, so on success this second
    # value *is* the acquisition warning — notably FlareSolverr's "may violate
    # publisher terms of service" notice. `retry_pdf` surfaces it; `attach_pdf`
    # hardcoded `"warnings": []` and dropped it, so the same acquisition
    # reported differently depending on which command performed it.
    local_pdf_path, acquisition_note = _store_pdf_source(
        source=source,
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        fetch_binary=fetch_binary,
        record=_record_at(read_result, index),
        filename_format=filename_format,
        fallback=_fallback_kwargs(config),
    )
    if local_pdf_path is None:
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "failed to attach PDF",
            # `_store_pdf_source` branches on the same prefix check: a URL
            # source failed because fetching it broke (an external
            # dependency, `REASON_UNAVAILABLE`, matching the "failed to fetch
            # PDF" case above); a local-path source failed because
            # `copy_pdf_to_papers_dir` could not find/read it, so
            # `REASON_NOT_FOUND` fits the common case. Imprecise on one rarer
            # sub-case: a local file that exists but isn't a valid PDF is
            # also reported as `REASON_NOT_FOUND` here, though it's really a
            # bad input (closer to `REASON_USAGE`) — `acquisition_note`'s text
            # is the only place that distinction currently survives, and
            # splitting on it would mean matching message text, which is the
            # anti-pattern this classification exists to remove. Left as an
            # honest imprecision rather than a wrong guess.
            "reason": REASON_UNAVAILABLE
            if source.startswith(("http://", "https://"))
            else REASON_NOT_FOUND,
            "warnings": [],
            "errors": _pdf_failure_errors(acquisition_note, "failed to attach PDF"),
        }

    update_result = _update_entry_keeping_pdf_consistent(
        lambda: update_bib_entry(
            bib["path"],
            citekey,
            lambda entry, record: _entry_with_pdf_fields(
                entry,
                cast(NormalizedRecord, dict(record)),
                local_pdf_path=local_pdf_path,
                pdf_url=source if source.startswith(("http://", "https://")) else None,
            ),
            file_path_style=config.get("pdf_file_path_style", "absolute"),
        ),
        new_pdf=local_pdf_path,
        existing_pdf_paths=existing_pdf_paths,
    )
    if not update_result["found"]:
        _remove_new_pdf(local_pdf_path, existing_pdf_paths)
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source": source,
            "message": "citekey disappeared",
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "citekey": citekey,
        "local_pdf_path": local_pdf_path,
        "source": source,
        "message": "attached PDF",
        "warnings": [
            note
            for note in (
                acquisition_note,
                _superseded_pdf_warning(_record_at(read_result, index), local_pdf_path),
            )
            if note
        ],
        "errors": [],
    }


def attach_pdf_bytes(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    pdf_base64: str,
    source_url: str | None,
) -> PdfAttachBytesResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "could not resolve target bib",
            "reason": REASON_CONFIG,
            "warnings": [],
            "errors": resolved.errors,
        }
    config, bib = resolved

    try:
        data = base64.b64decode(pdf_base64, validate=True)
    except (ValueError, TypeError):
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "invalid PDF payload",
            "reason": REASON_USAGE,
            "warnings": [],
            "errors": ["pdf_base64 must be valid base64"],
        }
    if not data.startswith(b"%PDF-"):
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "invalid PDF payload",
            "reason": REASON_USAGE,
            "warnings": [],
            "errors": ["decoded payload is not a PDF"],
        }

    return _attach_pdf_data(
        bib_name=bib["name"],
        bib_path=bib["path"],
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        data=data,
        source_url=source_url,
        filename_format=config.get("pdf_filename_format"),
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )


def attach_pdf_raw_bytes(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    citekey: str,
    pdf_bytes: bytes,
    source_url: str | None,
) -> PdfAttachBytesResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "bib_name": None,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "could not resolve target bib",
            "reason": REASON_CONFIG,
            "warnings": [],
            "errors": resolved.errors,
        }
    config, bib = resolved

    if not pdf_bytes.startswith(b"%PDF-"):
        return {
            "status": "error",
            "bib_name": bib["name"],
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "invalid PDF payload",
            "reason": REASON_USAGE,
            "warnings": [],
            "errors": ["pdf_bytes must start with %PDF-"],
        }

    return _attach_pdf_data(
        bib_name=bib["name"],
        bib_path=bib["path"],
        papers_dir=bib["papers_dir"],
        citekey=citekey,
        data=pdf_bytes,
        source_url=source_url,
        filename_format=config.get("pdf_filename_format"),
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    )


def _attach_pdf_data(
    *,
    bib_name: str,
    bib_path: str,
    papers_dir: str,
    citekey: str,
    data: bytes,
    source_url: str | None,
    filename_format: str | None = None,
    file_path_style: str = "absolute",
) -> PdfAttachBytesResult:
    read_result = read_bib_file(bib_path)
    entries = read_result["entries"]
    index = find_entry_index(entries, citekey)
    if index is None:
        return {
            "status": "error",
            "bib_name": bib_name,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "citekey not found",
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    existing_pdf_paths = _snapshot_pdf_paths(papers_dir)
    # Both arguments passed unconditionally: pdf_planning already handles a
    # falsy filename_format and a None record, so branching on them here only
    # duplicated that decision.
    destination = write_pdf_bytes(
        data=data,
        papers_dir=papers_dir,
        citekey=citekey,
        record=_record_at(read_result, index),
        filename_format=filename_format,
    )

    update_result = _update_entry_keeping_pdf_consistent(
        lambda: update_bib_entry(
            bib_path,
            citekey,
            lambda entry, record: _entry_with_pdf_fields(
                entry,
                cast(NormalizedRecord, dict(record)),
                local_pdf_path=destination,
                pdf_url=source_url,
            ),
            file_path_style=file_path_style,
        ),
        new_pdf=destination,
        existing_pdf_paths=existing_pdf_paths,
    )
    if not update_result["found"]:
        _remove_new_pdf(destination, existing_pdf_paths)
        return {
            "status": "error",
            "bib_name": bib_name,
            "citekey": citekey,
            "local_pdf_path": None,
            "source_url": source_url,
            "message": "citekey disappeared",
            "reason": REASON_NOT_FOUND,
            "warnings": [],
            "errors": [f"citekey not found: {citekey}"],
        }

    return {
        "status": "ok",
        "bib_name": bib_name,
        "citekey": citekey,
        "local_pdf_path": destination,
        "pdf_path": destination,
        "pdf_url": source_url,
        "pdf_status": "browser_saved",
        "pdf_error": None,
        "pdf_suggestion": None,
        "source_url": source_url,
        "message": "attached PDF bytes",
        "warnings": [],
        "errors": [],
    }


def _record_at(read_result: dict[str, Any], index: int) -> dict[str, object]:
    records = read_result.get("records")
    if isinstance(records, list) and 0 <= index < len(records):
        value = records[index]
        if isinstance(value, dict):
            return cast(dict[str, object], value)
    entries = read_result.get("entries")
    if isinstance(entries, list) and 0 <= index < len(entries):
        entry = entries[index]
        if isinstance(entry, dict):
            fields = entry.get("fields")
            if isinstance(fields, dict):
                return cast(dict[str, object], {"citekey": entry.get("citekey"), **fields})
    return {"citekey": ""}


def _store_pdf_source(
    *,
    source: str,
    papers_dir: str,
    citekey: str,
    fetch_binary: BinaryFetcher | None = None,
    record: dict[str, object] | None = None,
    filename_format: str | None = None,
    fallback: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Store a PDF from a URL or a local path.

    Mirrors `pdf_download.store_pdf_source`, but routes URLs through the full
    fallback chain rather than a single direct GET.
    """
    if source.startswith(("http://", "https://")):
        local_path, warning, error = fetch_and_store_pdf_with_fallbacks(
            url=source,
            papers_dir=papers_dir,
            citekey=citekey,
            fetch_binary=fetch_binary,
            record=cast(Any, record),
            filename_format=filename_format,
            **(fallback or {}),
        )
        return local_path, error or warning
    return copy_pdf_to_papers_dir(
        source_path=source,
        papers_dir=papers_dir,
        citekey=citekey,
        record=cast(Any, record),
        filename_format=filename_format,
    )


def _entry_with_pdf_fields(
    entry: BibtexEntry,
    record: NormalizedRecord,
    *,
    local_pdf_path: str,
    pdf_url: str | None,
) -> BibtexEntry:
    updated_record = dict(record)
    updated_record["local_pdf_path"] = local_pdf_path
    if pdf_url is not None:
        updated_record["pdf_url"] = pdf_url
    # Merge onto the existing entry: attaching a PDF must not rewrite the rest
    # of the entry from the record model and drop volume/pages/publisher/...
    return apply_record_to_entry(entry, cast(NormalizedRecord, updated_record))


# ---------------------------------------------------------------------------
# PDF text metadata extraction (merged from pdf_text_metadata.py)
# ---------------------------------------------------------------------------

_DOI_IN_TEXT_PATTERN = re.compile(r"(?i)\b(10\.\d{3,9}/[-._;()/:\w]+)\b")

_TITLE_SKIP_PREFIXES = (
    "doi:",
    "doi ",
    "http",
    "www.",
    "copyright",
    "\u00a9",
    "proceedings",
    "journal",
    "conference",
    "arxiv:",
    "received",
    "accepted",
    "published",
    "keywords:",
    "abstract",
    "introduction",
    "vol.",
    "pp.",
    "page",
    "fig.",
    "figure",
    "table",
    "issn",
    "isbn",
)

_TITLE_SKIP_PATTERNS = (
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"^\s*[-\u2013\u2014]+\s*$"),
    re.compile(r"^\s*\*\s*$"),
)


def empty_pdf_metadata(unreadable: str | None = None) -> PdfExtractionResult:
    """No metadata. *unreadable* says why, when the file could not be parsed.

    pypdf's verdict used to be discarded: a 0-byte, truncated or encrypted PDF
    was indistinguishable from a scan with no extractable text, so pzi stored a
    file it could not read and said nothing. `%PDF-` at the front was the only
    gate anything applied.
    """
    return {"doi": None, "title": None, "text_sample": None, "unreadable": unreadable}


def pdf_metadata_from_text(text: str) -> PdfExtractionResult:
    """Extract metadata from already-extracted PDF text."""
    if not text.strip():
        return empty_pdf_metadata()

    sample = text[:2000].strip() or None
    return {
        "doi": extract_doi_from_text(text),
        "title": extract_title_from_text(text),
        "text_sample": sample,
    }


def extract_doi_from_text(text: str) -> str | None:
    """Find first DOI in extracted text."""
    match = _DOI_IN_TEXT_PATTERN.search(text)
    if match is None:
        return None
    candidate = match.group(1).strip()
    candidate = re.sub(r"\s+", "", candidate)
    return candidate.lower()


def extract_title_from_text(text: str) -> str | None:
    """Return first non-empty line that looks like a paper title."""
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < 10:
            continue
        lower = stripped.lower()
        if any(lower.startswith(prefix) for prefix in _TITLE_SKIP_PREFIXES):
            continue
        if any(pattern.match(stripped) for pattern in _TITLE_SKIP_PATTERNS):
            continue
        if 10 <= len(stripped) <= 200:
            return stripped

    return None


def extract_pdf_metadata(path: str) -> PdfExtractionResult:
    """Extract DOI and title candidate from first pages of a PDF.

    A PDF that cannot be read yields no metadata — never an exception. pypdf
    raises its own ``PyPdfError`` subclasses (``PdfReadError``,
    ``EmptyFileError``, ``FileNotDecryptedError``, ``PdfStreamError``, …), none
    of which derive from ``OSError`` or ``ValueError``, so a 0-byte, truncated
    or encrypted PDF used to surface as a raw traceback from ``pzi add`` and a
    ``500`` from ``POST /capture``. Reading ``reader.pages`` is inside the guard
    too: the constructor is lazy, so an encrypted file only fails there.
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:
        return empty_pdf_metadata()

    file_path = Path(path)
    if not file_path.exists():
        return empty_pdf_metadata()

    text_pages: list[str] = []
    try:
        reader = PdfReader(str(file_path))
        pages = list(reader.pages[:3])
    except (OSError, ValueError, PyPdfError) as exc:
        # Reported rather than fatal. Some legitimate publisher PDFs defeat
        # pypdf, so refusing the acquisition would reject files the user can
        # open perfectly well; storing one silently is the other failure.
        return empty_pdf_metadata(f"{type(exc).__name__}: {exc}".strip().rstrip(":"))

    for page in pages:
        try:
            text = page.extract_text()
            if text:
                text_pages.append(text)
        except (OSError, ValueError, AttributeError, PyPdfError):
            continue

    full_text = "\n".join(text_pages)
    return pdf_metadata_from_text(full_text)
