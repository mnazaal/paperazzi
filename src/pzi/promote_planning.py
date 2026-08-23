"""Candidate discovery for preprint promotion: find a published version, explain why.

Split out of `promote_service`, which was 1441 lines for one feature. Everything
here is decision-making — building the query, calling the metadata providers,
filtering, scoring and reporting candidates — and none of it touches the bib
file. `promote_service` owns the writes; this module owns the answer to "is
there a published version of this preprint, and which one".
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any, cast
from urllib.error import HTTPError

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import ProviderBreaker
from pzi.identifiers import has_preprint_identity, is_preprint_doi
from pzi.metadata_sources import (
    fetch_crossref_record_by_title,
    fetch_dblp_record_by_title,
    fetch_openalex_record_by_title,
    fetch_openreview_record_by_title,
    fetch_semantic_scholar_record_by_title_with_error,
)
from pzi.protocols import (
    MetadataRecordFetcher,
    S2RecordWithErrorFetcher,
    SearchTranslationFetcher,
    accepts_keyword,
)
from pzi.resolution_match import score_match
from pzi.translation_server import fetch_search_translations


def find_published_candidate_with_diagnostics(
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
    breaker: ProviderBreaker | None = None,
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
            breaker=breaker,
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
    breaker: ProviderBreaker | None = None,
) -> NormalizedRecord | None:
    """Query one title-search provider, recording why it produced nothing.

    A provider that could not be reached must not be silently equivalent to one
    that answered "unknown": the first leaves the preprint unpromoted for a
    fixable reason, and only a recorded error says which.

    *breaker* is the run-level guard. A sweep over this library asks five
    providers about 13,462 preprints, so a provider that has stopped answering
    costs a timeout on every one of them for an answer already known. Skipping a
    tripped provider is not the same as it answering "unknown", so the skip is
    still recorded on `provider_errors` — the reason a preprint went unpromoted
    stays visible.
    """
    if breaker is not None and breaker.is_open(name):
        provider_errors.append(f"{name} (skipped — unreachable earlier in this run)")
        return None
    errors: list[str] = []
    try:
        candidate = _call_provider(
            fn, title, contact_email=contact_email, errors=errors
        )
    except (OSError, ValueError) as exc:
        provider_errors.append(f"{name} ({exc})")
        if breaker is not None:
            breaker.record_failure(name, str(exc))
        return None
    if errors:
        provider_errors.append(f"{name} ({errors[0]})")
        if breaker is not None:
            breaker.record_failure(name, errors[0])
        return None
    if breaker is not None:
        breaker.record_answer(name)
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


def published_candidate_confidence_warnings(
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
