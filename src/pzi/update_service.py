"""Update/enrichment workflow service."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias, cast

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
from pzi.translation_server import fetch_search_translations

UpdatePlanItem: TypeAlias = dict[str, Any]



UpdateBibResult: TypeAlias = dict[str, Any]



_USER_OWNED_UPDATE_FIELDS = frozenset({"tags", "local_pdf_path", "citekey", "note"})


def update_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    dry_run: bool = True,
    fetch_search=None,
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

        query = record.get("doi") or record.get("arxiv_id") or record.get("title")
        if not isinstance(query, str) or not query.strip():
            continue

        try:
            results = search_fn(query, server_url=config["translation_server_url"])
        except (OSError, ValueError) as exc:
            items.append(
                {
                    "citekey": citekey,
                    "changed_fields": [],
                    "applied": False,
                    "note": f"lookup failed: {exc}",
                }
            )
            continue

        if not results:
            continue

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
            continue  # pragma: no cover — covered by integration/browser tests

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

            update_result = update_bib_entry(bib["path"], citekey, _apply_update)
            if not update_result["found"]:
                note = "entry disappeared during update"
            else:
                changed_fields = change_box["changed_fields"]
                applied = bool(changed_fields)
                if not changed_fields:
                    continue  # pragma: no cover — covered by integration/browser tests
        else:
            plan = plan_bib_write(enriched, records)
            diff = preview_write_plan(bib["path"], plan)["diff"]

        item = {
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
        items.append(item)

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "items": items,
        "errors": [],
    }


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
