"""Local PDF capture helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias, cast

from pzi import add_planning as _add_planning
from pzi.add_planning import (
    _carry_item_type,
    fetch_record_for_input,
    merge_record_sources,
    select_best_metadata_result,
)
from pzi.bib_repository import WritePlan, read_bib_file
from pzi.bibtex import BibtexEntry, NormalizedRecord, bibtex_entry_to_record
from pzi.config import BibConfig
from pzi.identifiers import normalize_doi
from pzi.pdf import (
    NextPdfCandidate,
    fetch_and_store_pdf_trying_sources,
    remove_new_pdf,
    snapshot_pdf_paths,
)
from pzi.pdf_download import copy_pdf_to_papers_dir
from pzi.pdf_planning import plan_pdf_path
from pzi.pdf_service import extract_pdf_metadata
from pzi.protocols import (
    BinaryFetcher,
    MetadataRecordFetcher,
    S2RecordFetcher,
    SearchTranslationFetcher,
    WebTranslationFetcher,
)
from pzi.resolution_match import score_match
from pzi.similarity import find_exact_match
from pzi.translation_server import fetch_web_translations

AddRecordResult: TypeAlias = dict[str, Any]
FetchPdf = Callable[..., tuple[str | None, str | None, str | None]]
FetchRecord = Callable[..., tuple[NormalizedRecord, list[str], list[dict]]]
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
    contact_email: str | None = None,
    metadata_fetch_text: Callable[..., str] | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    errors: list[str] | None = None,
) -> NormalizedRecord:
    """Resolve a record for a local PDF from its embedded DOI or title.

    *errors* collects provider failures. Without it this path resolved metadata
    "best effort" and reported nothing, so ``--strict-metadata`` had nothing to
    act on and silently did not apply to local-PDF input.
    """
    raw_doi = extracted.get("doi")
    # Scraped out of the PDF's text, so it arrives with whatever surrounded it —
    # a `https://doi.org/` prefix, a trailing period from the sentence it ended.
    # Passing that through as already-`normalized` resolved it verbatim and
    # stored it that way; a value that is not a DOI at all (`see front matter`)
    # was resolved as one, wasting the request and risking a wrong adoption.
    doi = normalize_doi(raw_doi) if isinstance(raw_doi, str) else None
    if doi is not None:
        try:
            record, provider_errors, _results = fetch_record(
                raw_value=doi,
                classified={"kind": "doi", "raw": raw_doi, "normalized": doi},
                server_url=server_url,
                fetch_web=fetch_web,
                fetch_search=fetch_search,
                fetch_crossref=fetch_crossref,
                fetch_openalex=fetch_openalex,
                fetch_s2=fetch_s2,
                s2_api_key=s2_api_key,
                contact_email=contact_email,
                metadata_fetch_text=metadata_fetch_text,
                flaresolverr_url=flaresolverr_url,
                browser_pdf_cmd=browser_pdf_cmd,
            )
            if errors is not None:
                errors.extend(provider_errors)
            return record
        except (OSError, ValueError) as exc:
            if errors is not None:
                errors.append(str(exc))
            # Fall through rather than return. The PDF has already given us a
            # title and authors; returning here wrote a titleless DOI-only
            # record and threw them away, silently, because the DOI lookup
            # failed. The DOI is still worth keeping — it just is not all we
            # know.
            unresolved_doi = doi
        else:
            unresolved_doi = None
    else:
        unresolved_doi = None

    title = extracted.get("title")
    if isinstance(title, str) and title.strip():
        try:
            results = fetch_search(title, server_url=server_url)
        except (OSError, ValueError) as exc:
            if errors is not None:
                errors.append(str(exc))
            results = []
        fallback: NormalizedRecord = {"title": title.strip()}
        if unresolved_doi is not None:
            fallback["doi"] = unresolved_doi
        authors = extracted.get("authors")
        if isinstance(authors, list) and authors:
            fallback["authors"] = authors
        if results:
            return _adopt_title_search_hit(results, fallback, errors=errors)
        return fallback

    if unresolved_doi is not None:
        return {"doi": unresolved_doi}
    return {}


def _adopt_title_search_hit(
    results: Sequence[Mapping[str, object]],
    fallback: NormalizedRecord,
    *,
    errors: list[str] | None,
) -> NormalizedRecord:
    """Take the best search hit, but only if it is plausibly the same paper.

    The first result was taken whatever it was. A title-only match is the
    weakest evidence available — there is no DOI, and no author list from the
    PDF to corroborate with — so adopting the hit's DOI attaches a *different
    paper's* identifier to the user's file, which then dedupes against that
    paper and resolves to it on every later `update` and `check`.

    Judged by the same comparison `pzi check` uses to decide whether a source
    identified the work at all: a `title_mismatch` flag means it did not.
    """
    selected = select_best_metadata_result(results, fallback)
    found = selected.get("record")
    if not isinstance(found, Mapping):
        return fallback
    match = score_match(cast(Mapping[str, object], fallback), found)
    if "title_mismatch" in match["flags"]:
        if errors is not None:
            errors.append(
                "title search returned a different paper "
                f"({found.get('title')!r}, match {match['score']}/100); "
                "kept the title from the PDF and adopted no identifiers"
            )
        return fallback
    record = dict(found)
    # As the DOI and URL paths already do: without it every conference paper
    # captured from a local PDF became `@article` with `journal = {proceedings}`.
    _carry_item_type(record, selected)
    return cast(NormalizedRecord, record)


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
    if not isinstance(citekey, str) or not citekey.strip():
        return record, [], None

    if dry_run:
        # The preview names the file the real run writes, without copying a
        # byte. Returning early left `local_pdf_path` unset, so `--dry-run`
        # showed an entry with no `file =` line and the real run added one —
        # the preview describing a different entry from the one it previews.
        # `plan_pdf_path` is the pure half of `write_pdf_bytes`; only the
        # collision suffix (`-1`, `-2`) is unknowable without touching disk.
        planned = plan_pdf_path(
            papers_dir=papers_dir,
            citekey=citekey,
            record=record,
            filename_format=pdf_filename_format,
        )
        previewed = dict(record)
        previewed["local_pdf_path"] = str(planned)
        return cast(NormalizedRecord, previewed), [], None

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
    contact_email: str | None = None,
    metadata_fetch_text: Callable[..., str] | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    browser: str | None = None,
    browser_hook: bool = True,
    citekey_format: str | None = None,
    pdf_filename_format: str | None = None,
    strict_metadata: bool = False,
    # Everything below reaches the writer for every *other* input kind and
    # reached it for none of the local-PDF ones, because this call site simply
    # did not pass them. `--force-new` silently updated the existing entry
    # instead of inserting beside it, `pdf_file_path_style = "relative"` was
    # ignored so the library recorded absolute paths, and the `fallback_*`
    # overrides a caller supplies for a PDF with no usable metadata — the
    # commonest reason to add one by path — never took effect.
    force_new: bool = False,
    file_path_style: str = "absolute",
) -> AddRecordResult:
    read_result = read_bib_file(bib["path"])
    existing_records = [
        cast(NormalizedRecord, r) for r in read_result["records"]
    ]
    metadata_errors: list[str] = []
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
        contact_email=contact_email,
        metadata_fetch_text=metadata_fetch_text,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
        errors=metadata_errors,
    )
    # Gate before anything is written: reporting the strict failure after the
    # entry and its PDF had landed would leave exactly the half-verified record
    # the flag exists to prevent.
    if strict_metadata and metadata_errors:
        return _add_planning.error_result(
            message="metadata provider error (--strict-metadata)",
            errors=list(metadata_errors),
            dry_run=dry_run,
            warnings=[],
        )
    merged = merge_record_sources(base_record, record_overrides)
    # A provider error on this path used to be dropped on the floor unless
    # `--strict-metadata` was set, so a capture degraded by a Crossref 429 was
    # reported exactly like a clean one. Carried as warnings from here on, the
    # same wording `add_input_to_bib._finalize` uses for the other branch.
    provider_warnings = [f"provider error ({error})" for error in metadata_errors]
    # The acceptance gate. This branch returns before the one in
    # `add_service.add_input_to_bib`, so for a release that gate did not exist
    # here: `pzi add empty.pdf` wrote `@article{unknownxxxxuntitled}` carrying
    # nothing but a `file` field, at exit 0, and `--strict-metadata` — whose help
    # promises to "refuse to capture a paper the metadata does not identify" —
    # changed nothing. Refusing in both modes is the decision; the flag was never
    # the right place for it.
    if not _add_planning.identifies_a_paper(merged):
        return _add_planning.error_result(
            message="no metadata identifies this PDF",
            errors=_add_planning.minimum_metadata_diagnostics(merged),
            dry_run=dry_run,
            warnings=[
                *provider_warnings,
                "nothing was written: supply at least a title with "
                "--metadata-json FILE (or - for stdin) to add this PDF anyway",
            ],
        )
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
            force_new=force_new,
            file_path_style=file_path_style,
        )
    except Exception:
        remove_new_pdf(copied_local_path, existing_pdf_paths)
        raise
    result["warnings"] = [*provider_warnings, *warnings, *result["warnings"]]
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
    next_candidate: NextPdfCandidate | None = None,
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

    outcome = fetch_and_store_pdf_trying_sources(
        url=pdf_url,
        record=record,
        next_candidate=next_candidate,
        fetch_pdf=fetch_pdf,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
        browser=browser,
        browser_hook=browser_hook,
        filename_format=pdf_filename_format,
        api_url=api_url,
        api_auth_token=api_auth_token,
        desktop_fallback_hosts=desktop_fallback_hosts,
        ezproxy_host=ezproxy_host,
    )
    if outcome.local_pdf_path is None:
        return record, outcome.errors

    # From `outcome.record`, not `record`: a later candidate carries the
    # `pdf_url` and `pdf_source` that actually produced the file, and storing
    # the one that 403'd would misreport where it came from.
    updated = dict(outcome.record)
    updated["local_pdf_path"] = outcome.local_pdf_path
    warnings = [outcome.warning] if outcome.warning is not None else []
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

    # When force_new was used, both the old and new entries share the same DOI,
    # so the find_exact_match rebind below would return the *old* entry by
    # identity and throw away the force-generated citekey. The plan is already
    # correct in this case: plan_bib_write(force_new=True) sets the record to
    # the incoming one, whose citekey is already the suffixed key that gets
    # written. (The rebind-by-citekey loop that used to live here could not
    # change anything — it compared plan["record"]["citekey"] against itself.)
    if plan.get("force_new"):
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
    renamed_from = plan["record"].get("_citekey_renamed_from")
    if isinstance(renamed_from, str) and renamed_from and renamed_from != citekey:
        warnings = [
            *warnings,
            f"requested citekey {renamed_from!r} was already taken; "
            f"used {citekey!r} instead",
        ]

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


