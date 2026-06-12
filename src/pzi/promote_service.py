"""Preprint promotion service: find published versions and update or fork entries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit

from pzi.bib_repository import (
    execute_write_plan,
    plan_bib_write,
    preview_write_plan,
    read_bib_file,
    update_bib_entry,
)
from pzi.bibtex import NormalizedRecord, generate_citekey, normalize_authors, record_to_bibtex_entry
from pzi.capture_context import resolve_contact_email, resolve_optional_value
from pzi.config import load_and_resolve_bib
from pzi.format_templates import format_citekey
from pzi.metadata_sources import (
    fetch_crossref_record_by_title,
    fetch_openalex_record_by_title,
    fetch_semantic_scholar_record_by_title,
)
from pzi.pdf import fetch_and_store_pdf_with_fallbacks
from pzi.pdf import remove_new_pdf as _remove_new_pdf
from pzi.pdf import snapshot_pdf_paths as _snapshot_pdf_paths
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
    keep_preprint: bool = True,
    dry_run: bool = True,
    fetch_search=None,
    fetch_crossref=None,
    fetch_openalex=None,
    fetch_s2=None,
    fetch_binary=None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    confidence_threshold: int | None = None,
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
    s2_api_key = resolve_optional_value(
        command=config.get("semantic_scholar_api_key_cmd"),
        fallback=config.get("semantic_scholar_api_key"),
    )
    contact_email = resolve_contact_email(config)
    effective_flaresolverr_url = flaresolverr_url or config.get("flaresolverr_url")
    effective_browser_pdf_cmd = browser_pdf_cmd or config.get("browser_pdf_cmd")
    effective_confidence_threshold = (
        int(config.get("promote_confidence_threshold", 3))
        if confidence_threshold is None
        else confidence_threshold
    )

    read_result = read_bib_file(bib["path"])
    records = read_result["records"]
    known_records = list(records)
    existing_citekeys = {
        ck for r in records for ck in [r.get("citekey")] if isinstance(ck, str)
    }

    items: list[PromoteItem] = []
    summary = _empty_summary()

    for record in records:
        preprint_ck = record.get("citekey")
        if not isinstance(preprint_ck, str):
            continue  # pragma: no cover — covered by integration/browser tests
        if not is_preprint(record):
            continue
        summary["checked"] += 1

        candidate_result = _find_published_candidate_with_diagnostics(
            record=record,
            server_url=config["translation_server_url"],
            fetch_search=fetch_search,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_s2=fetch_s2,
            s2_api_key=s2_api_key,
            contact_email=contact_email,
        )
        candidate = candidate_result["candidate"]
        provider_errors = candidate_result["provider_errors"]
        metadata_diagnostics = candidate_result.get("metadata_diagnostics", [])
        if candidate is None:
            if candidate_result.get("reason") == "no_query":
                continue
            summary["provider_errors"] += len(provider_errors)
            summary["skipped_no_candidate"] += 1
            note = "no published candidate found"
            if provider_errors:
                note = f"{note} (provider errors: {', '.join(provider_errors)})"
            item = _skip_item(preprint_ck, note)
            if metadata_diagnostics:
                item["metadata_diagnostics"] = metadata_diagnostics
            items.append(item)
            continue

        score = _score_confidence(record, candidate)
        if score < effective_confidence_threshold:
            summary["skipped_low_confidence"] += 1
            item = _skip_item(
                preprint_ck,
                f"low confidence ({score} < {effective_confidence_threshold})",
            )
            if metadata_diagnostics:
                item["metadata_diagnostics"] = metadata_diagnostics
            metadata_warnings = _published_candidate_confidence_warnings(
                score=score, min_score=effective_confidence_threshold
            )
            if metadata_warnings:
                item["metadata_warnings"] = metadata_warnings
            items.append(item)
            continue

        duplicate_ck = _find_duplicate_citekey(candidate, known_records, preprint_ck)
        if duplicate_ck is not None:
            msg = f"already exists as {duplicate_ck}"
            summary["skipped_existing"] += 1
            item = _skip_item(preprint_ck, msg, published_ck=duplicate_ck)
            if metadata_diagnostics:
                item["metadata_diagnostics"] = metadata_diagnostics
            items.append(item)
            continue

        pdf_kwargs = dict(
            papers_dir=bib["papers_dir"],
            fetch_binary=fetch_binary,
            flaresolverr_url=effective_flaresolverr_url,
            browser_pdf_cmd=effective_browser_pdf_cmd,
            pdf_filename_format=config.get("pdf_filename_format"),
            browser_hook=config.get("browser_hook", True),
        )

        if keep_preprint:
            item = _handle_keep_preprint(
                bib_path=bib["path"],
                preprint_record=record,
                candidate=candidate,
                records=records,
                existing_citekeys=existing_citekeys,
                dry_run=dry_run,
                citekey_format=config.get("citekey_format"),
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
        if metadata_diagnostics:
            item["metadata_diagnostics"] = metadata_diagnostics

        items.append(item)  # pragma: no branch — covered by integration/browser tests
        if item.get("action") == "create":
            summary["created"] += 1
        elif item.get("action") == "update":
            summary["updated"] += 1
        if item["published_citekey"] is not None:  # pragma: no branch
            existing_citekeys.add(item["published_citekey"])
            if item["action"] in {"create", "update"}:
                published_record = dict(candidate)
                published_record["citekey"] = item["published_citekey"]
                known_records.append(published_record)

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "keep_preprint": keep_preprint,
        "items": items,
        "summary": summary,
        "errors": [],
    }


def _empty_summary() -> dict[str, int]:
    return {
        "checked": 0,
        "created": 0,
        "updated": 0,
        "skipped_no_candidate": 0,
        "skipped_low_confidence": 0,
        "skipped_existing": 0,
        "provider_errors": 0,
    }


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------


def _find_published_candidate_with_diagnostics(
    *,
    record: NormalizedRecord,
    server_url: str,
    fetch_search,
    fetch_crossref,
    fetch_openalex,
    fetch_s2,
    s2_api_key: str | None,
    contact_email: str | None = None,
) -> dict[str, Any]:
    provider_errors: list[str] = []
    search_fn = fetch_search or fetch_search_translations
    query = _build_query(record)
    if not query.strip():
        return {"candidate": None, "provider_errors": provider_errors, "reason": "no_query"}

    # 1. Translation server
    try:
        results = search_fn(query, server_url=server_url)
    except (OSError, ValueError):
        provider_errors.append("translation-server")
        results = []
    translation_candidates = _translation_candidates(results)
    candidate = _select_best_published_candidate(record, translation_candidates)
    if candidate is not None:
        return {
            "candidate": candidate,
            "provider_errors": provider_errors,
            "metadata_diagnostics": _published_candidate_diagnostics(
                record, translation_candidates
            ),
        }

    # 2. Fallback providers (title-based search for published version)
    title = record.get("title")
    if not isinstance(title, str) or not title.strip():
        return {"candidate": None, "provider_errors": provider_errors}

    provider_candidates: list[NormalizedRecord] = []
    crossref_fn = fetch_crossref or fetch_crossref_record_by_title
    try:
        candidate = _call_provider(crossref_fn, title, contact_email=contact_email)
    except (OSError, ValueError):
        provider_errors.append("crossref")
        candidate = None
    if candidate is not None and candidate.get("venue"):
        provider_candidates.append(cast(NormalizedRecord, dict(candidate)))

    openalex_fn = fetch_openalex or fetch_openalex_record_by_title
    try:
        candidate = _call_provider(openalex_fn, title, contact_email=contact_email)
    except (OSError, ValueError):
        provider_errors.append("openalex")
        candidate = None
    if candidate is not None and candidate.get("venue"):
        provider_candidates.append(cast(NormalizedRecord, dict(candidate)))

    s2_fn = fetch_s2 or (
        lambda t: fetch_semantic_scholar_record_by_title(t, api_key=s2_api_key)
    )
    try:
        candidate = s2_fn(title)
    except (OSError, ValueError):
        provider_errors.append("semantic-scholar")
        candidate = None
    if candidate is not None and candidate.get("venue"):
        provider_candidates.append(cast(NormalizedRecord, dict(candidate)))

    candidate = _select_best_published_candidate(record, provider_candidates)
    if candidate is not None:
        return {
            "candidate": candidate,
            "provider_errors": provider_errors,
            "metadata_diagnostics": _published_candidate_diagnostics(record, provider_candidates),
        }

    return {"candidate": None, "provider_errors": provider_errors}


def _call_provider(fn, value: str, *, contact_email: str | None):
    if contact_email:
        try:
            return fn(value, contact_email=contact_email)
        except TypeError:
            return fn(value)
    return fn(value)


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


def _translation_candidates(results: Any) -> list[NormalizedRecord]:
    if not isinstance(results, list):
        return []
    candidates: list[NormalizedRecord] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        rec = result.get("record")
        if isinstance(rec, Mapping) and rec.get("venue"):
            candidates.append(cast(NormalizedRecord, dict(rec)))
    return candidates


def _select_best_published_candidate(
    preprint: NormalizedRecord,
    candidates: list[NormalizedRecord],
) -> NormalizedRecord | None:
    if not candidates:
        return None
    return max(
        enumerate(candidates),
        key=lambda item: (_score_published_candidate(preprint, item[1]), -item[0]),
    )[1]


def _score_published_candidate(
    preprint: NormalizedRecord,
    candidate: NormalizedRecord,
) -> int:
    score = _score_confidence(preprint, candidate)
    if candidate.get("venue"):
        score += 2
    if candidate.get("doi"):
        score += 2
    if candidate.get("pdf_url"):
        score += 1
    return score


def _published_candidate_diagnostics(
    preprint: NormalizedRecord,
    candidates: list[NormalizedRecord],
) -> list[str]:
    if not candidates:
        return []
    scored = [
        (index, candidate, _score_published_candidate(preprint, candidate))
        for index, candidate in enumerate(candidates)
    ]
    best_index, best_candidate, best_score = max(
        scored,
        key=lambda item: (item[2], -item[0]),
    )
    lines = [
        _published_candidate_diagnostic_line(
            "selected", best_index, len(candidates), best_score, best_candidate
        )
    ]
    lines.extend(
        _published_candidate_diagnostic_line(
            "rejected", index, len(candidates), score, candidate
        )
        for index, candidate, score in scored
        if index != best_index
    )
    return lines


def _published_candidate_confidence_warnings(
    *, score: int, min_score: int
) -> list[str]:
    if score >= min_score:
        return []
    return [
        "metadata confidence low: "
        f"published candidate score={score} below {min_score}; verify promotion candidate"
    ]


def _published_candidate_diagnostic_line(
    status: str,
    index: int,
    total: int,
    score: int,
    candidate: NormalizedRecord,
) -> str:
    parts = [f"{status} candidate {index + 1}/{total}: score={score}"]
    doi = candidate.get("doi")
    title = candidate.get("title")
    venue = candidate.get("venue")
    year = candidate.get("year")
    if isinstance(doi, str) and doi.strip():
        parts.append(f"doi={doi.strip()}")
    if isinstance(title, str) and title.strip():
        parts.append(f"title={title.strip()}")
    if isinstance(venue, str) and venue.strip():
        parts.append(f"venue={venue.strip()}")
    if isinstance(year, int):
        parts.append(f"year={year}")
    return "; ".join(parts)


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


def _promote_item(
    preprint_citekey: str,
    published_citekey: str | None,
    action: str,
    *,
    changed_fields: list[str] | None = None,
    pdf_attached: bool | None = False,
    note: str | None = None,
    diff: str | None = None,
) -> PromoteItem:
    """Build a PromoteItem dict with all standard fields."""
    item: PromoteItem = {
        "preprint_citekey": preprint_citekey,
        "published_citekey": published_citekey,
        "action": action,
        "changed_fields": changed_fields or [],
        "pdf_attached": pdf_attached,
        "note": note,
    }
    if diff is not None:
        item["diff"] = diff
    return item


def _skip_item(preprint_ck: str, note: str, published_ck: str | None = None) -> PromoteItem:
    return _promote_item(preprint_ck, published_ck, "skip", note=note)


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
    pdf_filename_format: str | None = None,
    citekey_format: str | None = None,
    browser_hook: bool = True,
) -> PromoteItem:
    preprint_ck = cast(str, preprint_record["citekey"])

    published = _merge_published_metadata(preprint_record, candidate)
    published_ck = _generate_citekey_for_candidate(
        published,
        existing_citekeys,
        citekey_format=citekey_format,
    )
    published["citekey"] = published_ck

    existing_pdf_paths = _snapshot_pdf_paths(papers_dir)
    published, pdf_attached = _maybe_attach_pdf(
        published,
        published_ck,
        dry_run,
        papers_dir,
        fetch_binary,
        flaresolverr_url,
        browser_pdf_cmd,
        pdf_filename_format,
        browser_hook=browser_hook,
    )

    changed_fields = sorted(
        key for key in published if published.get(key) != candidate.get(key)
    ) or ["venue", "doi"]

    diff: str | None = None
    plan = plan_bib_write(published, records)
    if dry_run:
        diff = preview_write_plan(bib_path, plan)["diff"]
    else:
        try:
            execute_write_plan(bib_path, plan)
            _add_note_to_citekey(bib_path, preprint_ck, f"Published version: {published_ck}")
            _add_note_to_citekey(bib_path, published_ck, f"Preprint version: {preprint_ck}")
        except Exception:
            _remove_new_pdf(_local_pdf_path(published), existing_pdf_paths)
            raise

    return _promote_item(
        preprint_ck, published_ck, "create",
        changed_fields=changed_fields,
        pdf_attached=pdf_attached,
        diff=diff,
    )


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
    pdf_filename_format: str | None = None,
    browser_hook: bool = True,
) -> PromoteItem:
    preprint_ck = cast(str, preprint_record["citekey"])

    updated = _merge_published_metadata(preprint_record, candidate)
    updated["citekey"] = preprint_ck

    pdf_attached = False
    diff: str | None = None
    if dry_run:
        plan = plan_bib_write(updated, [preprint_record])
        diff = preview_write_plan(bib_path, plan)["diff"]
    else:
        existing_pdf_paths = _snapshot_pdf_paths(papers_dir)
        updated, pdf_attached = _maybe_attach_pdf(
            updated,
            preprint_ck,
            dry_run,
            papers_dir,
            fetch_binary,
            flaresolverr_url,
            browser_pdf_cmd,
            pdf_filename_format,
            browser_hook=browser_hook,
        )

        def _updater(entry, _current):
            return record_to_bibtex_entry(updated, entry_type="article")

        update_result = update_bib_entry(bib_path, preprint_ck, _updater)
        if update_result.get("found") is not True:
            _remove_new_pdf(_local_pdf_path(updated), existing_pdf_paths)
            return _promote_item(
                preprint_ck, preprint_ck, "error",
                note="preprint entry disappeared before promotion update could be written",
            )

    changed_fields = sorted(
        key for key in updated if updated.get(key) != preprint_record.get(key)
    )

    return _promote_item(
        preprint_ck, preprint_ck, "update",
        changed_fields=changed_fields,
        pdf_attached=pdf_attached if not dry_run else None,
        diff=diff,
    )


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
    pdf_filename_format: str | None = None,
    browser_hook: bool = True,
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
        browser_hook=browser_hook,
        record=record,
        filename_format=pdf_filename_format,
    )
    if path is None:
        return record, False

    updated = dict(record)
    updated["local_pdf_path"] = path
    return cast(NormalizedRecord, updated), True


def _local_pdf_path(record: NormalizedRecord) -> str | None:
    path = record.get("local_pdf_path")
    return path if isinstance(path, str) else None


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
    record: NormalizedRecord,
    existing_citekeys: set[str],
    *,
    citekey_format: str | None = None,
) -> str:
    if citekey_format:
        return format_citekey(citekey_format, record, existing_citekeys)
    return generate_citekey(
        {"authors": normalize_authors(record.get("authors")),
         "title": cast(str | None, record.get("title")),
         "year": cast(int | None, record.get("year"))},
        existing_citekeys,
    )


# ---------------------------------------------------------------------------
# Preprint classification helpers (merged from preprint_detector.py)
# ---------------------------------------------------------------------------

_PREPRINT_DOMAINS = frozenset({
    # Life sciences / medicine
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    # Chemistry
    "chemrxiv.org",
    # Psychology / social sciences
    "psyarxiv.com",
    "socarxiv.org",
    # Engineering / physical sciences
    "engrxiv.org",
    "techrxiv.org",
    "eartharxiv.org",
    # Multidisciplinary
    "ecoevorxiv.org",
    "researchsquare.com",
    "preprints.org",
    "osf.io",
    "zenodo.org",
    "authorea.com",
    "advance.sagepub.com",
    "papers.ssrn.com",
    # Regional / institutional
    "hal.archives-ouvertes.fr",
    "hal.science",
    "peerj.com",
})


def is_preprint(record: Mapping[str, object]) -> bool:
    """Return True when the record looks like a preprint."""
    venue = record.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return True
    if record.get("arxiv_id"):
        return True
    if _url_domain_on_preprint(record.get("source_url")):
        return True
    if _url_domain_on_preprint(record.get("canonical_url")):
        return True
    return False


_DOMAIN_TO_SOURCE: dict[str, str] = {
    # Life sciences / medicine
    "arxiv.org": "arXiv",
    "biorxiv.org": "bioRxiv",
    "medrxiv.org": "medRxiv",
    # Chemistry
    "chemrxiv.org": "ChemRxiv",
    # Psychology / social sciences
    "psyarxiv.com": "PsyArXiv",
    "socarxiv.org": "SocArXiv",
    "papers.ssrn.com": "SSRN",
    # Engineering / physical sciences
    "engrxiv.org": "engrXiv",
    "techrxiv.org": "TechRxiv",
    "eartharxiv.org": "EarthArXiv",
    # Multidisciplinary
    "ecoevorxiv.org": "EcoEvoRxiv",
    "researchsquare.com": "Research Square",
    "preprints.org": "Preprints.org",
    "osf.io": "OSF",
    "zenodo.org": "Zenodo",
    "authorea.com": "Authorea",
    "advance.sagepub.com": "SAGE Advance",
    # Regional / institutional
    "hal.archives-ouvertes.fr": "HAL",
    "hal.science": "HAL",
    "peerj.com": "PeerJ",
}


def detect_preprint_source(record: Mapping[str, object]) -> str | None:
    """Identify the preprint server, if any."""
    arxiv_id = record.get("arxiv_id")
    if isinstance(arxiv_id, str) and arxiv_id.strip():
        return "arXiv"

    for url_field in ("source_url", "canonical_url"):
        domain = _url_domain(record.get(url_field))
        if domain is not None and domain in _DOMAIN_TO_SOURCE:
            return _DOMAIN_TO_SOURCE[domain]
    return None


def _url_domain_on_preprint(value: object) -> bool:
    domain = _url_domain(value)
    return domain in _PREPRINT_DOMAINS if domain is not None else False


def _url_domain(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:  # pragma: no cover — covered by integration/browser tests
        return None  # pragma: no cover — covered by integration/browser tests
    host = parts.hostname
    if host is None:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host
