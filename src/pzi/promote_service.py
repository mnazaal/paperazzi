"""Preprint promotion service: find published versions and update or fork entries."""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast
from urllib.error import HTTPError

from pzi.add_planning import next_pdf_candidate_for_config
from pzi.bib_repository import (
    BatchWriteSession,
    ConcurrentEditError,
    WritePlan,
    backup_path_for,
    batch_write_session,
    plan_bib_write,
    preview_batch_write,
    preview_write_plan,
    read_bib_file,
    resolve_entry_type,
    update_bib_entry,
)
from pzi.bibtex import (
    USER_OWNED_FIELDS,
    BibtexEntry,
    NormalizedRecord,
    bibtex_entry_to_record,
    generate_citekey,
    merge_projected_entry,
    normalize_authors,
    record_to_bibtex_entry,
    venue_field_for_entry_type,
)
from pzi.bibtex import changed_fields as changed_fields_between
from pzi.capture_context import resolve_contact_email, resolve_optional_value
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.errors import REASON_CONFIG
from pzi.fetch_helpers import build_metadata_fetch_text
from pzi.format_templates import format_citekey
from pzi.identifiers import (
    has_preprint_identity,
    is_preprint_doi,
    is_preprint_url,
    names_a_preprint_server,
)
from pzi.metadata_sources import (
    fetch_crossref_record_by_title,
    fetch_dblp_record_by_title,
    fetch_openalex_record_by_title,
    fetch_openreview_record_by_title,
    fetch_semantic_scholar_record_by_title_with_error,
)
from pzi.pdf import NextPdfCandidate, fetch_and_store_pdf_trying_sources
from pzi.pdf import remove_new_pdf as _remove_new_pdf
from pzi.pdf import snapshot_pdf_paths as _snapshot_pdf_paths
from pzi.protocols import (
    BinaryFetcher,
    MetadataRecordFetcher,
    S2RecordWithErrorFetcher,
    SearchTranslationFetcher,
    accepts_keyword,
)
from pzi.resolution_match import score_match
from pzi.similarity import canonical_doi, normalize_title
from pzi.tag_service import add_tags
from pzi.translation_server import fetch_search_translations


class PromoteItem(TypedDict):
    """One preprint considered for promotion, inside a `PromoteResult`."""

    preprint_citekey: str
    published_citekey: str | None
    action: str
    changed_fields: list[str]
    pdf_attached: bool | None
    note: str | None
    #: Set when this preprint could not be promoted because something *went
    #: wrong* (as opposed to nothing to do). The runner reports PARTIAL from
    #: this; without it `exit_codes.PARTIAL` was unreachable and a run where
    #: every promotion failed exited 0 with `errors: []`.
    failed: NotRequired[bool]
    diff: NotRequired[str]
    #: Where the pre-promotion library was copied, on the `--replace` path. A
    #: backup nobody can find is not an undo.
    backup_path: NotRequired[str]
    metadata_diagnostics: NotRequired[list[str]]
    metadata_warnings: NotRequired[list[str]]


class PromoteResult(TypedDict):
    """A promotion sweep — what `pzi.promote()` returns.

    One `PromoteItem` per preprint the sweep considered, whether or not it was
    promoted. Previews by default; `dry_run` says which this was.
    """

    status: str
    bib_name: str | None
    dry_run: bool
    keep_preprint: bool
    items: list[PromoteItem]
    errors: list[str]
    summary: NotRequired[dict[str, Any]]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
#: A promotion must clear this on *title* similarity alone, on top of the
#: composite confidence gate. The composite is not enough: a published version
#: is the same paper, so the one thing that survives the preprint→published
#: transition is the title, while identical authors are exactly what makes the
#: dangerous case dangerous. Measured on 20 real preprints, every one of the 12
#: correct promotions scored `title 100`; the two wrong ones scored `title 62`
#: and `title 70` with `author 100`, and the composite was dragged over the
#: threshold of 60 by that perfect author match. Both were a *different paper by
#: the same authors* — "On Adaptivity and Confounding in **Contextual** Bandit
#: Experiments" for a multi-armed-bandit preprint, and a conference *demo*
#: paper for the system it demonstrates. 85 is `resolution_match`'s own mark for
#: a title strong enough to anchor a decision, and it sits clear of both.
_MIN_TITLE_SIMILARITY = 85

# Tag written to a preprint by `--mark-resolved` so re-runs can skip it.
_RESOLVED_TAG = "promoted"

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
    fetch_search: SearchTranslationFetcher | None = None,
    fetch_crossref: MetadataRecordFetcher | None = None,
    fetch_openalex: MetadataRecordFetcher | None = None,
    fetch_dblp: MetadataRecordFetcher | None = None,
    fetch_openreview: MetadataRecordFetcher | None = None,
    fetch_s2: S2RecordWithErrorFetcher | None = None,
    fetch_binary: BinaryFetcher | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    mark_resolved: bool = False,
) -> PromoteResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "reason": REASON_CONFIG,
            "bib_name": None,
            "dry_run": dry_run,
            "keep_preprint": keep_preprint,
            "items": [],
            "errors": resolved.errors,
        }
    config, bib = resolved
    s2_api_key = resolve_optional_value(
        command=config.get("semantic_scholar_api_key_cmd"),
        fallback=config.get("semantic_scholar_api_key"),
    )
    contact_email = resolve_contact_email(config)
    effective_flaresolverr_url = flaresolverr_url or config.get("flaresolverr_url")
    effective_browser_pdf_cmd = browser_pdf_cmd or config.get("browser_pdf_cmd")
    file_path_style = str(config.get("pdf_file_path_style", "absolute"))
    # Subscript, not `.get(..., default)`: `AppConfig` is a total TypedDict, so
    # the loader always supplies this. The old fallback of 3 predated the move
    # to `score_match`'s 0-100 scale (where the default is 60) and would have
    # meant an effectively open gate had it ever been reachable.
    effective_confidence_threshold = config["promote_confidence_threshold"]
    # Compose the metadata fetcher once (opt-in disk cache + per-host rate
    # limiting); the resolver uses it as the default for its title-search
    # providers unless a fetcher override is injected (e.g. by tests).
    metadata_fetch_text = build_metadata_fetch_text(config, api_key=s2_api_key)

    read_result = read_bib_file(bib["path"])
    records = read_result["records"]
    known_records = list(records)
    existing_citekeys = {
        ck for r in records for ck in [r.get("citekey")] if isinstance(ck, str)
    }

    items: list[PromoteItem] = []

    errors: list[str] = []
    summary = _empty_summary()
    resolved_preprints: list[str] = []
    # One backup for the run, not one per promoted entry. `backup_path_for` never
    # reuses a name and `update_bib_entry` copies the whole file, so computing it
    # inside the loop left a full copy of the library per `--replace` promotion:
    # against the 15.8 MB library configured here, promoting 100 preprints wrote
    # roughly 1.6 GB of `.bak` files that nothing ever cleans up. The first write
    # takes it — under its lock, immediately before writing, as `delete` and
    # `library merge` do — and the rest pass None.
    run_backup = _RunBackup(bib["path"])

    for record in records:
        preprint_ck = record.get("citekey")
        if not isinstance(preprint_ck, str):
            continue  # pragma: no cover — covered by integration/browser tests
        # `has_preprint_identity`, not `is_preprint`: the latter calls any
        # record without a `venue` a preprint, which is a large share of an
        # ordinary library, so promotion forked a second entry out of plain
        # @articles that merely lacked a `journal` field — manufacturing the
        # duplicates `pzi library dedupe` exists to report. `update_service` refuses
        # `is_preprint` here for exactly this reason and says so at its own call
        # site; the two commands now agree.
        if not has_preprint_identity(record):
            continue
        if mark_resolved and _RESOLVED_TAG in (record.get("tags") or []):
            # Already promoted on a previous --mark-resolved run; skip re-checking.
            summary["skipped_already_resolved"] += 1
            continue
        summary["checked"] += 1

        candidate_result = _find_published_candidate_with_diagnostics(
            record=record,
            server_url=config["translation_server_url"],
            fetch_search=fetch_search,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_dblp=fetch_dblp,
            fetch_openreview=fetch_openreview,
            fetch_s2=fetch_s2,
            s2_api_key=s2_api_key,
            contact_email=contact_email,
            metadata_fetch_text=metadata_fetch_text,
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

        # Gate on the explainable 0–100 breakdown, not a coarse feature count:
        # the old integer scale let three shared surnames reach the threshold on
        # their own, so a candidate flagged `title_mismatch` was written in
        # anyway while the diagnostics printed `confidence 0/100`.
        match = score_match(record, candidate)
        score = match["score"]
        title_similarity = match["title_similarity"]
        if score < effective_confidence_threshold or title_similarity < _MIN_TITLE_SIMILARITY:
            summary["skipped_low_confidence"] += 1
            reason = (
                f"low confidence ({score} < {effective_confidence_threshold})"
                if score < effective_confidence_threshold
                else f"title too different ({title_similarity} < {_MIN_TITLE_SIMILARITY}) "
                "— looks like a related paper, not this one's published version"
            )
            item = _skip_item(preprint_ck, reason)
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

        # Explainable breakdown of the score the gate above accepted (shown
        # under --verbose).
        match_line = (
            f"match confidence {match['score']}/100 "
            f"(title {match['title_similarity']}, author {match['author_similarity']})"
        )
        if match["flags"]:
            match_line += f"; flags: {', '.join(match['flags'])}"
        metadata_diagnostics = [match_line, *metadata_diagnostics]

        pdf_kwargs: dict[str, Any] = dict(
            papers_dir=bib["papers_dir"],
            fetch_binary=fetch_binary,
            flaresolverr_url=effective_flaresolverr_url,
            browser_pdf_cmd=effective_browser_pdf_cmd,
            pdf_filename_format=config.get("pdf_filename_format"),
            browser_hook=config.get("browser_hook", True),
            # Built once for the run: it resolves credentials, which do not vary
            # per entry. Without it `promote` retries one URL through every
            # transport and gives up, the same defect `add` and `retry` had.
            next_candidate=next_pdf_candidate_for_config(config, bib),
        )

        # Isolate the write/handler for one preprint: an unexpected failure here
        # (malformed candidate, mid-write error) must surface as an explainable
        # skip and let the rest of the library promote, not abort the whole run.
        # The handlers clean up any downloaded PDF before raising.
        try:
            if keep_preprint:
                item = _handle_keep_preprint(
                    bib_path=bib["path"],
                    preprint_record=record,
                    candidate=candidate,
                    existing_citekeys=existing_citekeys,
                    dry_run=dry_run,
                    citekey_format=config.get("citekey_format"),
                    file_path_style=file_path_style,
                    **pdf_kwargs,
                )
            else:
                item = _handle_update_in_place(
                    bib_path=bib["path"],
                    preprint_record=record,
                    candidate=candidate,
                    dry_run=dry_run,
                    file_path_style=file_path_style,
                    run_backup=run_backup,
                    **pdf_kwargs,
                )
        except Exception as exc:  # one failing entry must not abort the run
            summary["skipped_failed"] += 1
            items.append(
                _skip_item(preprint_ck, f"promotion failed: {exc}", failed=True)
            )
            errors.append(f"{preprint_ck}: promotion failed: {exc}")
            continue
        if metadata_diagnostics:
            item["metadata_diagnostics"] = metadata_diagnostics

        items.append(item)  # pragma: no branch — covered by integration/browser tests
        if item.get("action") in {"create", "update"}:
            # Keep mode leaves the preprint in place; tag it so a later
            # --mark-resolved run skips it.  Replace mode rewrites the entry to
            # the published version (no longer a preprint), so no tag is needed.
            if keep_preprint:
                resolved_preprints.append(preprint_ck)
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

    # Emit a top-level warning when S2 rate-limit failures accumulate.
    s2_rate_count = sum(
        1 for item in items
        if isinstance(item["note"], str)
        and "semantic-scholar (rate" in item["note"]
    )
    if s2_rate_count >= 2:
        key_configured = bool(
            resolve_optional_value(
                command=config.get("semantic_scholar_api_key_cmd"),
                fallback=config.get("semantic_scholar_api_key"),
            )
        )
        if not key_configured:
            summary["s2_warning"] = (
                f"{s2_rate_count} Semantic Scholar rate-limit failures. "
                "Configure semantic_scholar_api_key_cmd in config.toml for higher limits."
            )

    if mark_resolved and not dry_run and resolved_preprints:
        tagged, tag_failures = _tag_resolved(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekeys=resolved_preprints,
        )
        summary["marked_resolved"] = tagged
        errors.extend(tag_failures)

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "keep_preprint": keep_preprint,
        "items": items,
        "summary": summary,
        # Populated from the per-item failures above. Hardcoding `[]` meant a
        # run in which every promotion raised reported no errors at all.
        "errors": errors,
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "checked": 0,
        "created": 0,
        "updated": 0,
        "skipped_no_candidate": 0,
        "skipped_low_confidence": 0,
        "skipped_existing": 0,
        "skipped_already_resolved": 0,
        "skipped_failed": 0,
        "provider_errors": 0,
    }


def _tag_resolved(
    *, config_path: str, home_dir: str, bib_selector: str | None, citekeys: list[str]
) -> tuple[int, list[str]]:
    """Tag each promoted preprint with the resolved marker.

    Returns how many were actually tagged and what failed. Every result used to
    be discarded as "best-effort" while the caller set
    `marked_resolved = len(citekeys)` unconditionally — so a failed tag write
    reported success, the marker was absent, and the next run promoted the same
    preprint again. Best-effort is the right policy; claiming it worked is not.
    """
    tagged = 0
    failures: list[str] = []
    for citekey in citekeys:
        result = add_tags(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=citekey,
            tags=[_RESOLVED_TAG],
        )
        if result.get("status") == "ok":
            tagged += 1
        else:
            failures.append(
                f"{citekey}: could not mark resolved "
                f"({result.get('message') or 'tag write failed'})"
            )
    return tagged, failures


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------


def _find_published_candidate_with_diagnostics(
    *,
    record: NormalizedRecord,
    server_url: str,
    fetch_search: SearchTranslationFetcher | None,
    fetch_crossref: MetadataRecordFetcher | None,
    fetch_openalex: MetadataRecordFetcher | None,
    fetch_s2: S2RecordWithErrorFetcher | None,
    s2_api_key: str | None,
    contact_email: str | None = None,
    fetch_dblp: MetadataRecordFetcher | None = None,
    fetch_openreview: MetadataRecordFetcher | None = None,
    metadata_fetch_text: Callable[..., str] | None = None,
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

    # One loop, not five near-identical blocks. The blocks differed only in the
    # function called and the name reported, and each carried its own copy of
    # the `candidate.get("venue")` test — six copies in this function once the
    # translation-server path is counted. That repetition is why nothing ever
    # asked whether a candidate was *itself a preprint*: there was no single
    # place to ask it. Provider order is preserved (it is the tie-break in
    # `_select_best_published_candidate`), and DBLP/OpenReview still follow the
    # polite-pool providers as the CS-conference / ML-venue authorities that
    # confirm proceedings versions the DOI-based sources leave unresolved.
    provider_candidates: list[NormalizedRecord] = []
    title_providers: tuple[tuple[str, MetadataRecordFetcher | None, Any], ...] = (
        ("crossref", fetch_crossref, fetch_crossref_record_by_title),
        ("openalex", fetch_openalex, fetch_openalex_record_by_title),
        ("dblp", fetch_dblp, fetch_dblp_record_by_title),
        ("openreview", fetch_openreview, fetch_openreview_record_by_title),
    )
    for name, override, base_fn in title_providers:
        provider_fn = override or _default_provider_fn(base_fn, metadata_fetch_text)
        candidate = _try_provider(
            provider_fn, title, name=name,
            contact_email=contact_email, provider_errors=provider_errors,
        )
        # No venue means there is nothing to promote *to*; such a result was
        # never a candidate and is not reported as a rejection.
        if candidate is not None and candidate.get("venue"):
            provider_candidates.append(cast(NormalizedRecord, dict(candidate)))

    s2_fn: S2RecordWithErrorFetcher
    if fetch_s2 is not None:
        s2_fn = fetch_s2  # override already returns (record, error) tuple
    else:
        def _default_s2(t: str) -> tuple[NormalizedRecord | None, str | None]:
            return fetch_semantic_scholar_record_by_title_with_error(
                t, api_key=s2_api_key, fetch_text=metadata_fetch_text
            )
        s2_fn = _default_s2
    try:
        s2_candidate, s2_err = s2_fn(title)
    except HTTPError as exc:
        if exc.code in (403, 429):
            msg = "semantic-scholar (rate-limited"
            if s2_api_key is None:
                msg += " — configure semantic_scholar_api_key_cmd)"
            else:
                msg += " — check API key validity)"
            provider_errors.append(msg)
        else:
            provider_errors.append(f"semantic-scholar (HTTP {exc.code})")
        s2_candidate = None
        s2_err = None
    except (OSError, ValueError):
        provider_errors.append("semantic-scholar")
        s2_candidate = None
        s2_err = None
    if s2_candidate is not None and s2_candidate.get("venue"):
        provider_candidates.append(cast(NormalizedRecord, dict(s2_candidate)))
    elif s2_candidate is None and s2_err:
        err_lower = s2_err.lower()
        if "rate" in err_lower or "quota" in err_lower:
            msg = "semantic-scholar (rate-limited"
        elif "api key" in err_lower or "authorization" in err_lower:
            msg = "semantic-scholar (auth required"
        else:
            msg = f"semantic-scholar ({s2_err})"
        if s2_api_key is None:
            msg += " — configure semantic_scholar_api_key_cmd)"
        else:
            msg += ")"
        provider_errors.append(msg)

    candidate = _select_best_published_candidate(record, provider_candidates)
    if candidate is not None:
        return {
            "candidate": candidate,
            "provider_errors": provider_errors,
            "metadata_diagnostics": _published_candidate_diagnostics(record, provider_candidates),
        }

    # Nothing publishable — but say *why* when candidates were seen and all of
    # them were preprints. "no candidate" and "the only thing found was the
    # preprint again" are different answers, and the second is the common one
    # for a paper that genuinely was never published.
    if provider_candidates:
        return {
            "candidate": None,
            "provider_errors": provider_errors,
            "metadata_diagnostics": _published_candidate_diagnostics(
                record, provider_candidates
            ),
        }
    return {"candidate": None, "provider_errors": provider_errors}


def _default_provider_fn(
    base_fn: Callable[..., NormalizedRecord | None],
    fetch_text: Callable[..., str] | None,
) -> Callable[..., NormalizedRecord | None]:
    """Bind the composed (cached / rate-limited) fetcher to a title-search provider."""
    if fetch_text is None:
        return base_fn
    return functools.partial(base_fn, fetch_text=fetch_text)


def _call_provider(fn, value: str, *, contact_email: str | None, errors=None):
    kwargs: dict[str, Any] = {}
    if contact_email and accepts_keyword(fn, "contact_email"):
        kwargs["contact_email"] = contact_email
    # The real fetchers catch transport failures internally and return None, so
    # the errors channel is the only thing that separates "no match" from "never
    # reached". Injected fetchers need not accept it.
    if errors is not None and accepts_keyword(fn, "errors"):
        kwargs["errors"] = errors
    return fn(value, **kwargs)


def _try_provider(
    fn,
    title: str,
    *,
    name: str,
    contact_email: str | None,
    provider_errors: list[str],
) -> NormalizedRecord | None:
    """Query one title-search provider, recording why it produced nothing.

    A provider that could not be reached must not be silently equivalent to one
    that answered "unknown": the first leaves the preprint unpromoted for a
    fixable reason, and only a recorded error says which.
    """
    errors: list[str] = []
    try:
        candidate = _call_provider(
            fn, title, contact_email=contact_email, errors=errors
        )
    except (OSError, ValueError) as exc:
        provider_errors.append(f"{name} ({exc})")
        return None
    if errors:
        provider_errors.append(f"{name} ({errors[0]})")
        return None
    return candidate


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


def _translation_candidates(results: Any) -> list[NormalizedRecord]:
    if not isinstance(results, list):
        return []
    candidates: list[NormalizedRecord] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        rec = result.get("record")
        if isinstance(rec, Mapping) and rec.get("venue"):
            candidate = dict(rec)
            # The translation server reports the item type alongside the record,
            # not inside it. Carry it in, or `resolve_entry_type` has nothing to
            # go on and every promotion lands as `@article` — including
            # conference papers.
            item_type = result.get("item_type")
            if isinstance(item_type, str) and item_type.strip():
                candidate.setdefault("item_type", item_type.strip())
            candidates.append(cast(NormalizedRecord, candidate))
    return candidates


def _is_publishable_candidate(candidate: NormalizedRecord) -> bool:
    """Is this something a preprint can be promoted *to*?

    Two conditions, and the second is the one that was missing. A candidate must
    name a venue — there is nothing to promote to otherwise — and it must not
    itself carry preprint identity. `promote` already tests its *source* records
    with `has_preprint_identity` to decide what is worth promoting at all
    (see `promote_bib`); applying the same test to the candidate is the whole
    fix. Without it, "has a venue" stood in for "is published", and an arXiv
    record has a venue: `CoRR`, or `arXiv (Cornell University)`. Against 20 real
    preprints that produced 18 promotions of which only 3 were genuine
    publications — the rest were the preprint, relabelled as its own published
    version and cross-linked back to itself.
    """
    return bool(candidate.get("venue")) and not has_preprint_identity(candidate)


def _partition_candidates(
    candidates: list[NormalizedRecord],
) -> tuple[list[NormalizedRecord], list[NormalizedRecord]]:
    """Split into (promotable, preprints-that-cannot-be-a-published-version)."""
    publishable = [c for c in candidates if _is_publishable_candidate(c)]
    preprints = [c for c in candidates if not _is_publishable_candidate(c)]
    return publishable, preprints


def _select_best_published_candidate(
    preprint: NormalizedRecord,
    candidates: list[NormalizedRecord],
) -> NormalizedRecord | None:
    # Filtered here rather than at each provider, so no call site can forget.
    publishable, _ = _partition_candidates(candidates)
    if not publishable:
        return None
    return max(
        enumerate(publishable),
        key=lambda item: (_score_published_candidate(preprint, item[1]), -item[0]),
    )[1]


def _score_published_candidate(
    preprint: NormalizedRecord,
    candidate: NormalizedRecord,
) -> int:
    # Same 0–100 scale the acceptance gate uses, so the selected candidate and
    # the reported confidence can never disagree. The completeness bonuses stay
    # small: they break ties between comparably-matching candidates, they do not
    # promote a poor match over a good one.
    score = score_match(preprint, candidate)["score"]
    if candidate.get("venue"):
        score += 2
    # A DOI is evidence of a *publisher* record, which is the thing being looked
    # for — so arXiv's own DataCite DOI must not earn it. Rewarding it inverted
    # the comparison this function exists to make: for one real preprint the
    # arXiv record scored 105 with `10.48550/arxiv.2110.09348` while the actual
    # ICLR version, which carries no DOI at all, scored 103 and lost by exactly
    # this bonus. The published version was found and then rejected in favour of
    # the preprint it was supposed to replace.
    if candidate.get("doi") and not is_preprint_doi(candidate.get("doi")):
        score += 2
    if candidate.get("pdf_url"):
        score += 1
    return score


def _published_candidate_diagnostics(
    preprint: NormalizedRecord,
    candidates: list[NormalizedRecord],
) -> list[str]:
    publishable, preprints = _partition_candidates(candidates)
    total = len(candidates)
    # Preprint candidates are *reported*, not silently dropped. A filter with no
    # output turns "we considered the arXiv record and declined it" into an
    # unexplained absence, and this project's history is that a silent gate is
    # indistinguishable from a broken one.
    lines = [
        _published_candidate_diagnostic_line(
            "rejected (preprint)",
            index,
            total,
            _score_published_candidate(preprint, candidate),
            candidate,
        )
        for index, candidate in enumerate(preprints)
    ]
    if not publishable:
        return lines

    scored = [
        (index, candidate, _score_published_candidate(preprint, candidate))
        for index, candidate in enumerate(publishable)
    ]
    best_index, best_candidate, best_score = max(
        scored,
        key=lambda item: (item[2], -item[0]),
    )
    return [
        _published_candidate_diagnostic_line(
            "selected", best_index, total, best_score, best_candidate
        ),
        *(
            _published_candidate_diagnostic_line("rejected", index, total, score, candidate)
            for index, candidate, score in scored
            if index != best_index
        ),
        *lines,
    ]


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


def _find_duplicate_citekey(
    candidate: NormalizedRecord,
    records: list[NormalizedRecord],
    exclude_citekey: str,
) -> str | None:
    # Canonical comparison on both sides. Raw equality missed `10.1145/ABC` vs
    # `10.1145/abc` and `"Deep  Residual Learning"` (double space) vs
    # `"Deep Residual Learning"` — and the write that follows uses
    # `force_new=True`, so nothing downstream catches the duplicate it creates.
    c_doi = canonical_doi(candidate.get("doi"))
    c_title = normalize_title(candidate.get("title"))
    for rec in records:
        ck = rec.get("citekey")
        if not isinstance(ck, str) or ck == exclude_citekey:
            continue
        if c_doi and canonical_doi(rec.get("doi")) == c_doi:
            return ck
        if c_title and normalize_title(rec.get("title")) == c_title:
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
    backup_path: str | None = None,
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
    if backup_path is not None:
        item["backup_path"] = backup_path
    return item


def _skip_item(
    preprint_ck: str,
    note: str,
    published_ck: str | None = None,
    *,
    failed: bool = False,
) -> PromoteItem:
    item = _promote_item(preprint_ck, published_ck, "skip", note=note)
    if failed:
        item["failed"] = True
    return item


def _handle_keep_preprint(
    *,
    bib_path: str,
    preprint_record: NormalizedRecord,
    candidate: NormalizedRecord,
    existing_citekeys: set[str],
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
    pdf_filename_format: str | None = None,
    citekey_format: str | None = None,
    browser_hook: bool = True,
    file_path_style: str = "absolute",
    next_candidate: NextPdfCandidate | None = None,
) -> PromoteItem:
    preprint_ck = cast(str, preprint_record.get("citekey", ""))

    published = _merge_published_metadata(preprint_record, candidate)
    published_ck = _generate_citekey_for_candidate(
        published,
        existing_citekeys,
        citekey_format=citekey_format,
    )
    published["citekey"] = published_ck
    # The new entry's own cross-reference is known before it is written, so it
    # goes into the record rather than a follow-up update — one fewer write that
    # can leave the pair half-applied.
    back_reference = _append_note(published.get("note"), f"Preprint version: {preprint_ck}")
    if back_reference is not None:
        published["note"] = back_reference

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
        next_candidate=next_candidate,
    )

    # Diffed against the *preprint*, which is what the promotion changes — not
    # against the candidate, which listed the fields the candidate did not
    # supply and called them changes.
    changed_fields = changed_fields_between(preprint_record, published) or ["venue", "doi"]

    diff: str | None = None
    if dry_run:
        # Same `force_new` as the real write, so the preview shows the insert
        # the run will actually perform rather than a spurious in-place update.
        # Both writes, through the same builder the real run uses: the preview
        # used to show only the insert and omit the cross-reference note
        # stamped onto the preprint.
        diff = preview_batch_write(
            bib_path,
            lambda session: _apply_published_fork(
                session, published, preprint_ck, published_ck,
                bib_path=bib_path, check_collision=False,
            ),
            file_path_style=file_path_style,
        )["diff"]
    else:
        try:
            _write_published_fork(
                bib_path, published, preprint_ck, published_ck,
                file_path_style=file_path_style,
            )
        except Exception:
            _remove_new_pdf(_local_pdf_path(published), existing_pdf_paths)
            raise

    return _promote_item(
        preprint_ck, published_ck, "create",
        changed_fields=changed_fields,
        pdf_attached=pdf_attached,
        diff=diff,
    )


def _relocate_venue_for_entry_type(entry: BibtexEntry) -> BibtexEntry:
    """Move the venue to the key *entry*'s type expects.

    `merge_projected_entry` writes the venue back to whichever key the entry
    already used, because an ordinary update must not rewrite a proceedings
    entry's `booktitle` as `journal`. Promotion is the case where the type
    itself changes, so the venue's home has to follow it — otherwise a workshop
    paper promoted to a journal ends up an `@article` whose journal name sits in
    `booktitle`. Mutates and returns *entry*, whose ``fields`` the callers own.

    Only the retype-on-merge path needs this: fresh inserts go through
    `record_to_bibtex_entry`, which already picks the venue key from the entry
    type.
    """
    fields = entry["fields"]
    wanted = venue_field_for_entry_type(entry["entry_type"])
    stale = "journal" if wanted == "booktitle" else "booktitle"
    # Only relocate when the target is free: an entry carrying both keys is
    # already ambiguous, and dropping either would lose a venue.
    if stale in fields and wanted not in fields:
        fields[wanted] = fields.pop(stale)
    return entry


def _write_published_fork(
    bib_path: str,
    published: NormalizedRecord,
    preprint_ck: str,
    published_ck: str,
    *,
    file_path_style: str = "absolute",
) -> None:
    """Insert the published entry and cross-reference the preprint, atomically.

    Both edits go through one batch session so the pair cannot half-apply. The
    old sequence committed the insert first and stamped the preprint's note
    after, and only the note path refuses to patch a library containing a
    malformed block — so one broken entry anywhere in the file left the
    published entry committed (with a ``file =`` pointing at the PDF the
    rollback had just deleted) behind a reported ``created 0``.
    """
    with batch_write_session(bib_path, file_path_style=file_path_style) as session:
        _apply_published_fork(
            session,
            published,
            preprint_ck,
            published_ck,
            bib_path=bib_path,
            check_collision=True,
        )


def _apply_published_fork(
    session: BatchWriteSession,
    published: NormalizedRecord,
    preprint_ck: str,
    published_ck: str,
    *,
    bib_path: str,
    check_collision: bool,
) -> None:
    """Fold both of the fork's writes into *session*.

    Shared by the real write and the dry-run preview, so the preview cannot
    show a different pair of writes than the run performs.
    """
    if check_collision and any(
        record.get("citekey") == published_ck for record in session.records
    ):
        # `published_ck` was generated against the snapshot read at the top of
        # the run, but this session re-reads under the lock. The single-write
        # path reconciles that drift in `_rebase_insert_plan_against_current`,
        # which a batch session does not go through — so the collision is
        # checked here, and with `force_new` set nothing else would. This is
        # the only `ConcurrentEditError` left in the tree: everywhere else, a
        # concurrent edit is absorbed rather than refused.
        raise ConcurrentEditError(
            f"citekey {published_ck} appeared in {bib_path} while promoting "
            f"{preprint_ck}; aborting rather than writing a duplicate entry"
        )
    # force_new: the published record is forked from the preprint and can
    # still share an identity with it, so an identity-matched plan would
    # turn this insert into an in-place update of the preprint itself. The
    # candidate was already checked against the library by
    # `_find_duplicate_citekey`, so there is nothing legitimate to match.
    session.apply_plan(plan_bib_write(published, session.records, force_new=True))
    note_plan = _plan_note_update(
        session, preprint_ck, f"Published version: {published_ck}"
    )
    if note_plan is not None:
        session.apply_plan(note_plan)


def _plan_note_update(
    session: BatchWriteSession, citekey: str, text: str
) -> WritePlan | None:
    """Plan a note append for *citekey*, or None when there is nothing to do."""
    for index, record in enumerate(session.records):
        if record.get("citekey") != citekey:
            continue
        new_note = _append_note(record.get("note"), text)
        if new_note is None:
            return None
        updated = cast(NormalizedRecord, {**record, "note": new_note})
        return {
            "action": "update",
            "index": index,
            "record": updated,
            # Pre-merged onto the entry on disk, like every other update plan:
            # both sinks apply `plan["entry"]` verbatim, so a bare projection
            # here would drop every field the record model does not carry.
            "entry": merge_projected_entry(
                session.entries[index],
                record_to_bibtex_entry(
                    updated, entry_type=session.entries[index]["entry_type"]
                ),
            ),
            "changed_fields": ["note"],
        }
    return None


def _promoted_entry(
    entry: BibtexEntry,
    current_record: NormalizedRecord,
    candidate: NormalizedRecord,
    *,
    pdf_source: NormalizedRecord | None = None,
) -> BibtexEntry:
    """The entry a promotion writes in place. Shared by the write and the preview.

    Merges the published metadata onto the record as it is on disk *now*, not
    onto the pre-lock snapshot: `update_bib_entry` re-reads under the exclusive
    lock precisely so a concurrent edit is not thrown away.
    """
    locked = _merge_published_metadata(
        cast(NormalizedRecord, dict(current_record)), candidate
    )
    # The PDF is acquired before the lock is taken, so its path lives on the
    # pre-lock record and has to be carried over onto the fresh merge.
    for pdf_field in ("local_pdf_path", "pdf_url"):
        value = (pdf_source or {}).get(pdf_field)
        if value:
            locked[pdf_field] = value  # type: ignore[literal-required]
    # Project onto the entry rather than replacing it: a rebuild from the record
    # drops every field `NormalizedRecord` does not model (`volume`, `pages`,
    # `publisher`, ...).
    projected = record_to_bibtex_entry(locked, entry_type=resolve_entry_type(locked))
    merged = merge_projected_entry(entry, projected)
    # `merge_projected_entry` keeps the on-disk entry type, because an ordinary
    # update has no business retyping an entry. Promotion is the exception — the
    # preprint has become a published paper — so the type is resolved from the
    # promoted record rather than left as `@unpublished`.
    if projected["entry_type"] != merged["entry_type"]:
        merged["entry_type"] = projected["entry_type"]
        _relocate_venue_for_entry_type(merged)
    return merged


def _preview_in_place_update(
    bib_path: str,
    preprint_ck: str,
    candidate: NormalizedRecord,
    *,
    file_path_style: str,
) -> str | None:
    """The diff `--replace` will produce, built from the same entry projection."""
    read_result = read_bib_file(bib_path)
    entries = read_result["entries"]
    records = read_result["records"]
    index = next(
        (i for i, entry in enumerate(entries) if entry["citekey"] == preprint_ck),
        None,
    )
    if index is None:  # pragma: no cover — the caller just read this citekey
        return None
    merged_entry = _promoted_entry(entries[index], records[index], candidate)
    plan: WritePlan = {
        "action": "update",
        "index": index,
        "record": bibtex_entry_to_record(merged_entry),
        "entry": merged_entry,
        "changed_fields": [],
    }
    return preview_write_plan(bib_path, plan, file_path_style=file_path_style)["diff"]


class _RunBackup:
    """The single ``.bak`` a promote run leaves, taken by whichever write is first.

    ``update_bib_entry`` copies the whole bib to the path it is given, so passing
    a fresh path per entry meant a full copy per promotion, and passing the *same*
    path per entry would overwrite the original with an already-promoted state.
    Handing the path to the first write only gets both right: one copy, of the
    library as it was before the run.
    """

    def __init__(self, bib_path: str) -> None:
        self._bib_path = bib_path
        self._path: Path | None = None
        self._taken = False

    def take(self) -> Path | None:
        """The backup path for the next write, or None once one has been taken."""
        if self._taken:
            return None
        self._taken = True
        self._path = backup_path_for(self._bib_path, "promote")
        return self._path

    @property
    def path(self) -> Path | None:
        """The backup that was written, or None if nothing was backed up."""
        if self._path is not None and self._path.exists():
            return self._path
        return None


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
    file_path_style: str = "absolute",
    next_candidate: NextPdfCandidate | None = None,
    run_backup: _RunBackup | None = None,
) -> PromoteItem:
    preprint_ck = cast(str, preprint_record.get("citekey", ""))

    updated = _merge_published_metadata(preprint_record, candidate)
    updated["citekey"] = preprint_ck

    pdf_attached = False
    diff: str | None = None
    backup: str | None = None
    if dry_run:
        # Target the preprint *by citekey*, exactly as the real write does with
        # `update_bib_entry`. Relying on identity matching produced an INSERT
        # here — `_merge_published_metadata` strips `arxiv_id`, so nothing
        # matched — and the preview told the user their original entry survived
        # when `--replace` overwrites it.
        diff = _preview_in_place_update(
            bib_path,
            preprint_ck,
            candidate,
            file_path_style=file_path_style,
        )
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
            next_candidate=next_candidate,
        )

        def _updater(entry, current):
            return _promoted_entry(entry, current, candidate, pdf_source=updated)

        # `--replace` overwrites the preprint entry with a *different* paper's
        # metadata and deliberately strips its identity (`eprint`, the arXiv
        # DOI, the preprint URL). That is destruction of the same kind `delete`
        # and `library merge` back up, and it had no undo at all.
        #
        # One backup for the whole run — see `_RunBackup`. This was
        # `backup_path_for(bib_path, preprint_ck)`, i.e. a full copy of the
        # library per promoted entry.
        backup_target = (
            run_backup.take() if run_backup is not None
            else backup_path_for(bib_path, preprint_ck)
        )
        update_result = update_bib_entry(
            bib_path,
            preprint_ck,
            _updater,
            file_path_style=file_path_style,
            backup_path=backup_target,
        )
        if update_result.get("found") is not True:
            _remove_new_pdf(_local_pdf_path(updated), existing_pdf_paths)
            return _promote_item(
                preprint_ck, preprint_ck, "error",
                note="preprint entry disappeared before promotion update could be written",
            )
        # Written only when the write actually changed something, so its
        # existence is the honest test of whether there is anything to undo.
        # With a run-level backup, every promoted entry reports the same path:
        # it is the one file that undoes the run.
        if run_backup is not None:
            existing_backup = run_backup.path
            backup = str(existing_backup) if existing_backup is not None else None
        else:
            backup = (
                str(backup_target)
                if backup_target is not None and backup_target.exists()
                else None
            )

    # Over the union of both key sets: iterating `updated` alone could only ever
    # report fields that survived, so a field the promotion *removed* — the
    # `eprint`, the preprint URL, the arXiv DOI — was applied but never named.
    changed_fields = changed_fields_between(preprint_record, updated)

    return _promote_item(
        preprint_ck, preprint_ck, "update",
        changed_fields=changed_fields,
        pdf_attached=pdf_attached if not dry_run else None,
        diff=diff,
        backup_path=backup,
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
    next_candidate: NextPdfCandidate | None = None,
) -> tuple[NormalizedRecord, bool]:
    pdf_url = record.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip() or dry_run:
        return record, False

    outcome = fetch_and_store_pdf_trying_sources(
        url=pdf_url,
        record=record,
        next_candidate=next_candidate,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
        browser_hook=browser_hook,
        filename_format=pdf_filename_format,
    )
    if outcome.local_pdf_path is None:
        return record, False

    # `outcome.record`, so a promotion that fell back to another source records
    # the URL that produced the file rather than the one that failed.
    updated = dict(outcome.record)
    updated["local_pdf_path"] = outcome.local_pdf_path
    return cast(NormalizedRecord, updated), True


def _local_pdf_path(record: NormalizedRecord) -> str | None:
    path = record.get("local_pdf_path")
    return path if isinstance(path, str) else None


def _merge_published_metadata(
    preprint: NormalizedRecord, candidate: NormalizedRecord,
) -> NormalizedRecord:
    merged = dict(preprint)
    for key, value in candidate.items():
        if key in USER_OWNED_FIELDS:
            continue
        # A candidate key the provider could not fill is *absent* metadata, not
        # an instruction to clear the preprint's. `_openreview_normalize` always
        # emits `doi: None`, so copying it blindly deletes a populated DOI.
        if value is None or value == "" or value == []:
            continue
        merged[key] = value
    merged["tags"] = list(preprint.get("tags") or [])

    # The result describes the *published* version, so it must not inherit the
    # preprint's identity. Beyond being wrong metadata, an inherited identity is
    # load-bearing twice over: `find_exact_match` keys on `canonical_url`, so a
    # kept URL makes the published insert collide with the preprint it came
    # from, and `resolve_entry_type` keys on the preprint hosts, so it would
    # type the published entry `@unpublished`.
    merged.pop("arxiv_id", None)
    # `10.48550/arXiv.…` is arXiv's own DataCite DOI: it identifies the
    # *preprint*, so keeping it labels the published entry with the version it
    # just stopped being — and a later `pzi library check` resolves it straight back to
    # the preprint. Only dropped when the candidate offered no DOI of its own;
    # when it did, the loop above has already overwritten this.
    if is_preprint_doi(merged.get("doi")):
        merged.pop("doi", None)
    for url_field in ("canonical_url", "source_url"):
        if candidate.get(url_field):
            continue
        if is_preprint_url(merged.get(url_field)):
            merged.pop(url_field, None)
    # Better BibTeX writes `publisher = {arXiv}` and `number = {arXiv:2110.09348}`
    # onto its arXiv entries, and both rode the merge into the published record:
    # a paper promoted to ICLR claimed arXiv as its publisher and kept the
    # preprint's identifier as its issue number. Invisible until the rest of
    # this was fixed, because the "published" record used to *be* the arXiv one,
    # where those fields looked correct. Same rule as the DOI above — dropped
    # only when the candidate offered nothing of its own.
    if not candidate.get("publisher") and names_a_preprint_server(
        merged.get("publisher")
    ):
        merged.pop("publisher", None)
    for locator in ("number", "volume"):
        if not candidate.get(locator) and _is_arxiv_locator(merged.get(locator)):
            merged.pop(locator, None)
    return cast(NormalizedRecord, merged)


#: `arXiv:2110.09348` in `number`, `abs/2112.15246` in `volume` — arXiv's own
#: locators as Better BibTeX and DBLP write them. Anchored, so a real issue
#: number or volume cannot match.
_ARXIV_LOCATOR_RE = re.compile(r"^(?:arxiv:|abs/)", re.IGNORECASE)


def _is_arxiv_locator(value: object) -> bool:
    """True when a ``number``/``volume`` is really an arXiv identifier."""
    return isinstance(value, str) and bool(_ARXIV_LOCATOR_RE.match(value.strip()))


#: arXiv's DataCite prefix. Deliberately just this one: bioRxiv and medRxiv
#: share `10.1101/` with Cold Spring Harbor Laboratory Press's journals, so the
#: prefix alone cannot tell a preprint from a published paper there.


def _append_note(existing: object, text: str) -> str | None:
    """Append *text* to a note, or return None when it is already there."""
    note_str = str(existing) if existing is not None else ""
    if text in note_str:
        return None
    return f"{note_str}; {text}" if note_str else text


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
