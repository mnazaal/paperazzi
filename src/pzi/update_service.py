"""Update/enrichment workflow service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict, cast

from pzi.add_planning import (
    metadata_result_confidence_warnings,
    metadata_result_diagnostics,
    select_best_metadata_result,
)
from pzi.bib_repository import (
    plan_bib_write,
    preview_write_plan,
    read_bib_file,
    update_bib_entry,
)
from pzi.bibtex import NormalizedRecord, record_to_bibtex_entry
from pzi.config import load_and_resolve_bib
from pzi.protocols import SearchTranslationFetcher
from pzi.translation_server import fetch_search_translations


class UpdatePlanItem(TypedDict):
    citekey: str
    changed_fields: list[str]
    applied: bool
    note: str | None
    diff: NotRequired[str]
    metadata_diagnostics: NotRequired[list[str]]
    metadata_warnings: NotRequired[list[str]]


class UpdateBibResult(TypedDict):
    status: str
    bib_name: str | None
    dry_run: bool
    items: list[UpdatePlanItem]
    errors: list[str]



_USER_OWNED_UPDATE_FIELDS = frozenset({"tags", "local_pdf_path", "citekey", "note"})


def update_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    dry_run: bool = True,
    fetch_search: SearchTranslationFetcher | None = None,
) -> UpdateBibResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "dry_run": dry_run,
            "items": [],
            "errors": resolved,
        }
    config, bib = resolved
    search_fn = fetch_search or fetch_search_translations
    metadata_confidence_min_score = int(config.get("metadata_confidence_min_score", 0))
    read_result = read_bib_file(bib["path"])
    records = read_result["records"]

    items: list[UpdatePlanItem] = []

    for record in records:
        citekey = record.get("citekey")
        if not isinstance(citekey, str):
            continue  # pragma: no cover — covered by integration/browser tests
        if not _needs_update(record):
            continue
        # Isolate each record: a malformed candidate or a mid-update failure
        # must not abort the whole pass.  (The lookup itself is already reported
        # as a per-item note inside the helper; this guards everything else.)
        try:
            item = _plan_update_for_record(
                cast(NormalizedRecord, record),
                citekey,
                bib_path=bib["path"],
                server_url=str(config["translation_server_url"]),
                search_fn=search_fn,
                records=cast("list[NormalizedRecord]", records),
                dry_run=dry_run,
                metadata_confidence_min_score=metadata_confidence_min_score,
            )
        except Exception as exc:  # noqa: BLE001 — one bad record must not abort the run
            failed: UpdatePlanItem = {
                "citekey": citekey,
                "changed_fields": [],
                "applied": False,
                "note": f"update failed: {exc}",
            }
            item = failed
        if item is not None:
            items.append(item)

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "items": items,
        "errors": [],
    }


def _plan_update_for_record(
    record: NormalizedRecord,
    citekey: str,
    *,
    bib_path: str,
    server_url: str,
    search_fn: SearchTranslationFetcher,
    records: list[NormalizedRecord],
    dry_run: bool,
    metadata_confidence_min_score: int,
) -> UpdatePlanItem | None:
    """Plan (and, unless *dry_run*, apply) one record's metadata enrichment.

    Returns the per-item result, or ``None`` when there is nothing to do (no
    query, no results, or no changed fields).  A network lookup failure is
    reported as a per-item note rather than raised; any *other* error is left to
    propagate so the caller's per-record guard can record it without aborting
    the whole update pass.
    """
    query = record.get("doi") or record.get("arxiv_id") or record.get("title")
    if not isinstance(query, str) or not query.strip():
        return None

    try:
        results = search_fn(query, server_url=server_url)
    except (OSError, ValueError) as exc:
        return {
            "citekey": citekey,
            "changed_fields": [],
            "applied": False,
            "note": f"lookup failed: {exc}",
        }

    if not results:
        return None

    selected = select_best_metadata_result(
        cast(list[Mapping[str, Any]], results),
        cast(Mapping[str, object], record),
    )
    metadata_diagnostics = metadata_result_diagnostics(
        cast(list[Mapping[str, Any]], results),
        cast(Mapping[str, object], record),
    )
    metadata_warnings = metadata_result_confidence_warnings(
        cast(list[Mapping[str, Any]], results),
        cast(Mapping[str, object], record),
        min_score=metadata_confidence_min_score,
    )
    candidate = selected["record"]
    changed_fields = _changed_fields_for_candidate(record, candidate)
    if not changed_fields:
        return None  # pragma: no cover — covered by integration/browser tests

    applied = False
    note: str | None = None
    diff: str | None = None
    enriched = _conservative_enrich(
        cast(NormalizedRecord, dict(record)),
        cast(NormalizedRecord, dict(candidate)),
    )
    if not dry_run:
        change_box: dict[str, list[str]] = {"changed_fields": []}

        def _apply_update(entry, current_record):
            current_enriched = _conservative_enrich(
                cast(NormalizedRecord, dict(current_record)),
                cast(NormalizedRecord, dict(candidate)),
            )
            change_box["changed_fields"] = _changed_fields(current_record, current_enriched)
            if not change_box["changed_fields"]:
                return entry  # pragma: no cover — covered by integration/browser tests
            return record_to_bibtex_entry(current_enriched, entry_type=entry["entry_type"])

        update_result = update_bib_entry(bib_path, citekey, _apply_update)
        if not update_result["found"]:
            note = "entry disappeared during update"
        else:
            changed_fields = change_box["changed_fields"]
            applied = bool(changed_fields)
            if not changed_fields:
                return None  # pragma: no cover — covered by integration/browser tests
    else:
        plan = plan_bib_write(enriched, records)
        diff = preview_write_plan(bib_path, plan)["diff"]

    item: UpdatePlanItem = {
        "citekey": citekey,
        "changed_fields": changed_fields,
        "applied": applied if not dry_run else False,
        "note": note,
    }
    if diff is not None:
        item["diff"] = diff
    if metadata_diagnostics:
        item["metadata_diagnostics"] = metadata_diagnostics
    if metadata_warnings:
        item["metadata_warnings"] = metadata_warnings
    return item


def _needs_update(record: Mapping[str, object]) -> bool:
    venue = record.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return True
    if record.get("arxiv_id") and not record.get("doi"):
        return True
    if not record.get("year"):
        return True
    return False


def _conservative_enrich(
    existing: NormalizedRecord, incoming: NormalizedRecord
) -> NormalizedRecord:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in _USER_OWNED_UPDATE_FIELDS:
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
    return cast(NormalizedRecord, merged)


def _changed_fields_for_candidate(
    existing: Mapping[str, object], candidate: Mapping[str, object]
) -> list[str]:
    enriched = _conservative_enrich(
        cast(NormalizedRecord, dict(existing)),
        cast(NormalizedRecord, dict(candidate)),
    )
    return _changed_fields(existing, enriched)


def _changed_fields(
    existing: Mapping[str, object], updated: Mapping[str, object]
) -> list[str]:
    return sorted(key for key in updated.keys() if updated.get(key) != existing.get(key))
