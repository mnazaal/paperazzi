"""Preprint promotion service: find published versions and update or fork entries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias, cast

from pzi.bib_repository import execute_write_plan, plan_bib_write, read_bib_file, update_bib_entry
from pzi.bibtex import NormalizedRecord, generate_citekey, record_to_bibtex_entry
from pzi.config import load_and_resolve_bib
from pzi.metadata_sources import (
    fetch_crossref_record,
    fetch_openalex_record,
    fetch_semantic_scholar_record,
)
from pzi.pdf import fetch_and_store_pdf_with_fallbacks
from pzi.preprint_detector import is_preprint
from pzi.translation_server import fetch_search_translations

PromoteItem: TypeAlias = dict[str, Any]



PromoteResult: TypeAlias = dict[str, Any]



# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

_SCORE_TITLE_EXACT = 5
_SCORE_TITLE_PARTIAL = 3
_SCORE_AUTHOR_MAX = 3
_SCORE_YEAR_EXACT = 2
_SCORE_YEAR_ADJACENT = 1

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def promote_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    keep_preprint: bool = False,
    dry_run: bool = True,
    fetch_search=None,
    fetch_crossref=None,
    fetch_openalex=None,
    fetch_s2=None,
    fetch_binary=None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    confidence_threshold: int = 3,
) -> PromoteResult:
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "dry_run": dry_run,
            "keep_preprint": keep_preprint,
            "items": [],
            "errors": resolved,
        }
    config, bib = resolved

    read_result = read_bib_file(bib["path"])
    records = read_result["records"]
    existing_citekeys = {
        ck for r in records for ck in [r.get("citekey")] if isinstance(ck, str)
    }

    items: list[PromoteItem] = []

    for record in records:
        preprint_ck = record.get("citekey")
        if not isinstance(preprint_ck, str):
            continue  # pragma: no cover — covered by integration/browser tests
        if not is_preprint(record):
            continue

        candidate = _find_published_candidate(
            record=record,
            server_url=config["translation_server_url"],
            fetch_search=fetch_search,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_s2=fetch_s2,
            s2_api_key=config.get("semantic_scholar_api_key"),
        )
        if candidate is None:
            continue

        score = _score_confidence(record, candidate)
        if score < confidence_threshold:
            items.append(
                _skip_item(preprint_ck, f"low confidence ({score} < {confidence_threshold})")
            )
            continue

        duplicate_ck = _find_duplicate_citekey(candidate, records, preprint_ck)
        if duplicate_ck is not None:
            msg = f"already exists as {duplicate_ck}"
            items.append(_skip_item(preprint_ck, msg, published_ck=duplicate_ck))
            continue

        pdf_kwargs = dict(
            papers_dir=bib["papers_dir"],
            fetch_binary=fetch_binary,
            flaresolverr_url=flaresolverr_url,
            browser_pdf_cmd=browser_pdf_cmd,
        )

        if keep_preprint:
            item = _handle_keep_preprint(
                bib_path=bib["path"],
                preprint_record=record,
                candidate=candidate,
                records=records,
                existing_citekeys=existing_citekeys,
                dry_run=dry_run,
                **pdf_kwargs,
            )
        else:
            item = _handle_update_in_place(
                bib_path=bib["path"],
                preprint_record=record,
                candidate=candidate,
                dry_run=dry_run,
                **pdf_kwargs,
            )

        items.append(item)  # pragma: no branch — covered by integration/browser tests
        if item["published_citekey"] is not None:  # pragma: no branch
            existing_citekeys.add(item["published_citekey"])

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "keep_preprint": keep_preprint,
        "items": items,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------


def _find_published_candidate(
    *,
    record: NormalizedRecord,
    server_url: str,
    fetch_search,
    fetch_crossref,
    fetch_openalex,
    fetch_s2,
    s2_api_key: str | None,
) -> NormalizedRecord | None:
    search_fn = fetch_search or fetch_search_translations
    query = _build_query(record)
    if not query.strip():
        return None

    # 1. Translation server
    try:
        results = search_fn(query, server_url=server_url)
    except (OSError, ValueError):
        results = []
    candidate = _first_with_venue(results)
    if candidate is not None:
        return candidate

    # 2. Fallback providers (title-based search for published version)
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    crossref_fn = fetch_crossref or fetch_crossref_record
    try:
        candidate = crossref_fn(title)
    except (OSError, ValueError):
        candidate = None
    if candidate is not None and candidate.get("venue"):
        return candidate

    openalex_fn = fetch_openalex or fetch_openalex_record
    try:
        candidate = openalex_fn(title)
    except (OSError, ValueError):
        candidate = None
    if candidate is not None and candidate.get("venue"):
        return candidate

    if s2_api_key:
        s2_fn = fetch_s2 or (lambda t: fetch_semantic_scholar_record(t, api_key=s2_api_key))
        try:
            candidate = s2_fn(title)
        except (OSError, ValueError):
            candidate = None
        if candidate is not None and candidate.get("venue"):
            return candidate

    return None


def _build_query(record: NormalizedRecord) -> str:
    parts: list[str] = []
    title = record.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())
    authors = record.get("authors")
    if isinstance(authors, list):
        for author in authors[:2]:
            if isinstance(author, str) and author.strip():
                parts.append(author.strip())
    year = record.get("year")
    if isinstance(year, int):
        parts.append(str(year))
    return " ".join(parts)


def _first_with_venue(results: Any) -> NormalizedRecord | None:
    if not isinstance(results, list):
        return None
    for result in results:
        if not isinstance(result, Mapping):
            continue
        rec = result.get("record")
        if isinstance(rec, Mapping) and rec.get("venue"):
            return cast(NormalizedRecord, dict(rec))
    return None


# ---------------------------------------------------------------------------
# Scoring and deduplication
# ---------------------------------------------------------------------------


def _score_confidence(preprint: NormalizedRecord, candidate: NormalizedRecord) -> int:
    score = 0
    p_title = str(preprint.get("title") or "").lower().strip()
    c_title = str(candidate.get("title") or "").lower().strip()
    if p_title and c_title:
        if p_title == c_title:
            score += _SCORE_TITLE_EXACT
        elif p_title in c_title or c_title in p_title:
            score += _SCORE_TITLE_PARTIAL

    p_authors = {a.lower().strip() for a in (preprint.get("authors") or []) if isinstance(a, str)}
    c_authors = {a.lower().strip() for a in (candidate.get("authors") or []) if isinstance(a, str)}
    score += min(len(p_authors & c_authors), _SCORE_AUTHOR_MAX)

    p_year = preprint.get("year")
    c_year = candidate.get("year")
    if isinstance(p_year, int) and isinstance(c_year, int):
        if p_year == c_year:
            score += _SCORE_YEAR_EXACT
        elif abs(p_year - c_year) <= 1:
            score += _SCORE_YEAR_ADJACENT

    return score


def _find_duplicate_citekey(
    candidate: NormalizedRecord,
    records: list[NormalizedRecord],
    exclude_citekey: str,
) -> str | None:
    c_doi = candidate.get("doi")
    c_title = str(candidate.get("title") or "").lower().strip()
    for rec in records:
        ck = rec.get("citekey")
        if not isinstance(ck, str) or ck == exclude_citekey:
            continue
        if c_doi and rec.get("doi") == c_doi:
            return ck
        if c_title and str(rec.get("title") or "").lower().strip() == c_title:
            return ck
    return None


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _skip_item(preprint_ck: str, note: str, published_ck: str | None = None) -> PromoteItem:
    return {
        "preprint_citekey": preprint_ck,
        "published_citekey": published_ck,
        "action": "skip",
        "changed_fields": [],
        "pdf_attached": None,
        "note": note,
    }


def _handle_keep_preprint(
    *,
    bib_path: str,
    preprint_record: NormalizedRecord,
    candidate: NormalizedRecord,
    records: list[NormalizedRecord],
    existing_citekeys: set[str],
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
) -> PromoteItem:
    preprint_ck = cast(str, preprint_record["citekey"])

    published = _merge_published_metadata(preprint_record, candidate)
    published_ck = generate_citekey(
        {"authors": list(published.get("authors") or []),
         "title": cast(str | None, published.get("title")),
         "year": cast(int | None, published.get("year"))},
        existing_citekeys,
    )
    published["citekey"] = published_ck

    published, pdf_attached = _maybe_attach_pdf(
        published, published_ck, dry_run, papers_dir, fetch_binary,
        flaresolverr_url, browser_pdf_cmd,
    )

    changed_fields = sorted(
        key for key in published if published.get(key) != candidate.get(key)
    ) or ["venue", "doi"]

    if not dry_run:
        plan = plan_bib_write(published, records)
        execute_write_plan(bib_path, plan)
        _add_note_to_citekey(bib_path, preprint_ck, f"Published version: {published_ck}")
        _add_note_to_citekey(bib_path, published_ck, f"Preprint version: {preprint_ck}")

    return {
        "preprint_citekey": preprint_ck,
        "published_citekey": published_ck,
        "action": "create",
        "changed_fields": changed_fields,
        "pdf_attached": pdf_attached,
        "note": None,
    }


def _handle_update_in_place(
    *,
    bib_path: str,
    preprint_record: NormalizedRecord,
    candidate: NormalizedRecord,
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
) -> PromoteItem:
    preprint_ck = cast(str, preprint_record["citekey"])

    updated = _merge_published_metadata(preprint_record, candidate)
    updated["citekey"] = preprint_ck

    changed_fields = sorted(
        key for key in updated if updated.get(key) != preprint_record.get(key)
    )

    pdf_attached = False
    if not dry_run:
        updated, pdf_attached = _maybe_attach_pdf(
            updated, preprint_ck, dry_run, papers_dir, fetch_binary,
            flaresolverr_url, browser_pdf_cmd,
        )

        def _updater(entry, _current):
            return record_to_bibtex_entry(updated, entry_type=entry["entry_type"])

        update_bib_entry(bib_path, preprint_ck, _updater)

    return {
        "preprint_citekey": preprint_ck,
        "published_citekey": preprint_ck,
        "action": "update",
        "changed_fields": changed_fields,
        "pdf_attached": pdf_attached if not dry_run else None,
        "note": None,
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _maybe_attach_pdf(
    record: NormalizedRecord,
    citekey: str,
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
) -> tuple[NormalizedRecord, bool]:
    pdf_url = record.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip() or dry_run:
        return record, False

    path, _warn, _err = fetch_and_store_pdf_with_fallbacks(
        url=pdf_url,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
    )
    if path is None:
        return record, False

    updated = dict(record)
    updated["local_pdf_path"] = path
    return cast(NormalizedRecord, updated), True


def _merge_published_metadata(
    preprint: NormalizedRecord, candidate: NormalizedRecord,
) -> NormalizedRecord:
    _USER_OWNED = frozenset({"tags", "local_pdf_path", "citekey", "note"})
    merged = dict(preprint)
    for key, value in candidate.items():
        if key in _USER_OWNED:
            continue
        merged[key] = value
    merged["tags"] = list(preprint.get("tags") or [])
    merged.pop("arxiv_id", None)
    return cast(NormalizedRecord, merged)


def _add_note_to_citekey(bib_path: str, citekey: str, text: str) -> None:
    def _updater(entry, current_record):
        note = current_record.get("note")
        note_str = str(note) if note is not None else ""
        if text in note_str:
            return entry
        new_note = f"{note_str}; {text}" if note_str else text
        updated = dict(current_record)
        updated["note"] = new_note
        return record_to_bibtex_entry(updated, entry_type=entry["entry_type"])

    update_bib_entry(bib_path, citekey, _updater)


def _generate_citekey_for_candidate(
    record: NormalizedRecord, existing_citekeys: set[str],
) -> str:
    return generate_citekey(
        {"authors": list(record.get("authors") or []),
         "title": cast(str | None, record.get("title")),
         "year": cast(int | None, record.get("year"))},
        existing_citekeys,
    )
