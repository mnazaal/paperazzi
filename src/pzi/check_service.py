"""Reference validation service: verify entries against authoritative sources.

`check_bib` is a read-only audit — it never writes the library.  For each entry
it runs a short cascade of metadata sources (Crossref → OpenAlex → DBLP →
OpenReview → Semantic Scholar), short-circuiting as soon as one produces a
high-confidence match, and assigns a three-way verdict:

* ``verified``          — every claimed field is positively confirmed
* ``could_not_verify``  — a record was found but a field could not be confirmed,
                          or nothing matched at all (abstention, *not* a pass)
* ``problematic``       — positive evidence of a defect (title/author/year
                          mismatch, chimeric citation, fabricated author, …)

This catches fabricated / hallucinated references (relevant to arXiv's 2026
hallucinated-reference policy) without writing anything or requiring the Zotero
translation-server — it uses HTTP metadata sources only, so it runs in CI.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Literal, NotRequired, TypedDict

from pzi.bib_repository import read_bib_file_with_failures
from pzi.bibtex import NormalizedRecord
from pzi.capture_context import resolve_contact_email, resolve_optional_value
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.fetch_helpers import build_metadata_fetch_text
from pzi.metadata_sources import (
    fetch_crossref_record_by_title,
    fetch_dblp_record_by_title,
    fetch_openalex_record_by_title,
    fetch_openreview_record_by_title,
    fetch_semantic_scholar_record_by_title,
)
from pzi.resolution_match import MatchScore, _title_similarity, score_match

Verdict = Literal["verified", "could_not_verify", "problematic"]

# A short-circuit: once a source confirms this strongly, stop querying slower ones.
_SHORT_CIRCUIT = 95
# Minimum overall confidence to call an entry verified.
_VERIFIED_BAR = 80
_VERIFIED_BAR_STRICT = 90

# Flags that constitute positive evidence of a defect (→ problematic).
_PROBLEMATIC_FLAGS = frozenset(
    {
        "title_mismatch",
        "author_mismatch",
        "chimeric",
        "fabricated_author",
        "authors_swapped",
        "author_truncated",
        "future_year",
        "year_mismatch",
        "doi_mismatch",
        "given_name_substitution",
    }
)


class CheckItem(TypedDict):
    citekey: str
    verdict: Verdict
    confidence_score: int
    flags: list[str]
    mismatches: list[str]
    #: Sources that answered. A source that failed is *not* listed here — the
    #: distinction between "consulted and did not know it" and "never reached"
    #: is the whole difference between a fabricated reference and a bad network.
    sources_checked: list[str]
    #: One message per source that could not be consulted.
    source_errors: NotRequired[list[str]]


class CheckResult(TypedDict):
    status: str
    bib_name: str | None
    strict: bool
    total: int
    counts: dict[str, int]
    items: list[CheckItem]
    errors: list[str]


# Title-search providers in throughput-aware order: polite-pool DOI sources
# first, CS/ML authorities next, the keyless S2 fallback last.
_Provider = tuple[str, Callable[[str, list[str]], NormalizedRecord | None]]


def _providers(
    *,
    fetch_text: Callable[..., str] | None,
    s2_api_key: str | None,
    contact_email: str | None,
    overrides: Mapping[str, Callable[..., NormalizedRecord | None] | None],
) -> list[_Provider]:
    def bind(name: str, base: Callable[..., NormalizedRecord | None]) -> _Provider:
        override = overrides.get(name)
        if override is not None:
            # Injected fetchers (tests, callers) take only a title; they perform
            # no I/O, so they have nothing to report on the errors channel.
            return (name, lambda title, _errors: override(title))
        return (
            name,
            lambda title, errors: base(
                title, contact_email=contact_email, fetch_text=fetch_text, errors=errors
            ),
        )

    s2_override = overrides.get("s2")
    s2_fn = (
        (lambda title, _errors: s2_override(title))
        if s2_override is not None
        else (
            lambda title, errors: fetch_semantic_scholar_record_by_title(
                title, api_key=s2_api_key, fetch_text=fetch_text, errors=errors
            )
        )
    )
    return [
        bind("crossref", fetch_crossref_record_by_title),
        bind("openalex", fetch_openalex_record_by_title),
        bind("dblp", fetch_dblp_record_by_title),
        bind("openreview", fetch_openreview_record_by_title),
        ("s2", s2_fn),
    ]


def _impossible_year(record: Mapping[str, object], *, now_year: int) -> bool:
    year = record.get("year")
    return isinstance(year, int) and (year > now_year + 1 or year < 1500)


def _verify_entry(
    record: NormalizedRecord,
    providers: list[_Provider],
    *,
    strict: bool,
    now_year: int,
) -> CheckItem:
    citekey = str(record.get("citekey") or "")
    title = record.get("title")

    # A future / impossible year is positive evidence of fabrication on its own.
    base_flags: list[str] = []
    base_mismatches: list[str] = []
    if _impossible_year(record, now_year=now_year):
        base_flags.append("future_year")
        base_mismatches.append(f"year {record.get('year')} is implausible")

    if not isinstance(title, str) or not title.strip():
        return {
            "citekey": citekey,
            "verdict": "problematic" if base_flags else "could_not_verify",
            "confidence_score": 0,
            "flags": base_flags,
            "mismatches": base_mismatches or ["entry has no title to verify"],
            "sources_checked": [],
        }

    sources_checked: list[str] = []
    source_errors: list[str] = []
    source_notes: list[str] = []
    scored: list[tuple[str, MatchScore]] = []
    for name, fetch in providers:
        provider_errors: list[str] = []
        try:
            candidate = fetch(title, provider_errors)
        except (OSError, ValueError) as exc:
            # The documented failure modes of the fetchers: transport and decode.
            source_errors.append(f"{name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — a provider bug must not abort the run
            source_errors.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            continue
        if provider_errors:
            # Reached the call but got no answer — a network or API failure, which
            # says nothing about the entry. Recording it as a *checked* source is
            # what let an offline run report every entry as unverifiable with all
            # sources checked and no errors: indistinguishable from the sources
            # having been consulted and none of them knowing the paper, which is
            # the fabricated-reference signal this command exists to raise.
            source_errors.append(f"{name}: {provider_errors[0]}")
            continue
        sources_checked.append(name)
        if candidate is None:
            continue
        if _is_unrelated_hit(record, candidate):
            # A by-title search returns its top hit whatever it is — Crossref
            # always answers — so a paper a source does not index came back as
            # some *other* paper, scored `title_mismatch`, and the tool accused
            # a genuine citation of being fabricated. A hit this far from the
            # title is a search miss, not evidence: the source simply does not
            # have this work, which is `could_not_verify`.
            source_notes.append(f"{name}: no matching record found")
            continue
        # Authors confirmed by ≥2 sources earn a confidence bonus.
        confirming = 1 + sum(
            1 for _n, s in scored if s["author_similarity"] >= 60
        )
        match = score_match(record, candidate, author_sources=confirming, strict=strict)
        scored.append((name, match))
        if match["score"] >= _SHORT_CIRCUIT and not strict:
            break

    return _verdict_from_scores(
        citekey,
        scored,
        sources_checked,
        strict=strict,
        base_flags=base_flags,
        base_mismatches=base_mismatches,
        source_errors=source_errors,
    )


#: Below this title similarity the candidate is a different work, not a worse
#: version of this one. `_TITLE_OK` (60) is where a *match* becomes suspicious;
#: this is where a "match" stops being one at all.
_UNRELATED_TITLE = 30


def _is_unrelated_hit(
    record: Mapping[str, object], candidate: Mapping[str, object]
) -> bool:
    """True when the candidate is plainly a different paper.

    Only titles are compared: a by-title search that missed returns something
    with a different title, while a genuine match with a *typo* stays well
    above the floor.
    """
    return _title_similarity(
        _record_title(record), _record_title(candidate)
    ) < _UNRELATED_TITLE


def _record_title(record: Mapping[str, object]) -> str | None:
    title = record.get("title")
    return title if isinstance(title, str) else None


def _verdict_from_scores(
    citekey: str,
    scored: list[tuple[str, MatchScore]],
    sources_checked: list[str],
    *,
    strict: bool,
    base_flags: list[str],
    base_mismatches: list[str],
    source_errors: list[str] | None = None,
) -> CheckItem:
    errors = list(source_errors or [])
    if not scored:
        # Nothing matched anywhere: abstain (not a clean pass), unless a base
        # defect (impossible year) already condemns it.
        if not sources_checked and errors:
            # Nothing answered at all. Saying "no source could confirm this" here
            # would read as evidence against the entry when the truth is that the
            # run never reached a source.
            reason = "no source could be reached (see source_errors)"
        else:
            reason = "no source could confirm this reference"
        return {
            "citekey": citekey,
            "verdict": "problematic" if base_flags else "could_not_verify",
            "confidence_score": 0,
            "flags": base_flags,
            "mismatches": base_mismatches or [reason],
            "sources_checked": sources_checked,
            "source_errors": errors,
        }

    best_name, best = max(scored, key=lambda item: item[1]["score"])
    # Defect evidence is collected from *every* source that produced it, not
    # only the top scorer. Taking flags from `best` alone let a sparse
    # title-only record that happened to score higher suppress a Crossref
    # record's `doi_mismatch` — the tool's whole job is to surface exactly that.
    flags = [*base_flags, *_defect_flags_across_sources(scored)]
    mismatches = [*base_mismatches, *_mismatch_lines_across_sources(scored)]
    bar = _VERIFIED_BAR_STRICT if strict else _VERIFIED_BAR

    if any(f in _PROBLEMATIC_FLAGS for f in flags):
        verdict: Verdict = "problematic"
    elif best["score"] >= bar and "author_unknown" not in flags:
        verdict = "verified"
    else:
        # A source carrying no author list cannot corroborate authorship, and a
        # title match alone is exactly what a fabricated citation reproduces.
        # That is not evidence of a defect either, so it lands here rather than
        # in `problematic`: unconfirmed, not wrong.
        verdict = "could_not_verify"

    contributions = [f"best match via {best_name}: {c}" for c in best["contributions"]]
    return {
        "citekey": citekey,
        "verdict": verdict,
        "confidence_score": best["score"],
        "flags": flags,
        "mismatches": mismatches or contributions,
        "sources_checked": sources_checked,
        "source_errors": errors,
    }


def _defect_flags_across_sources(scored: list[tuple[str, MatchScore]]) -> list[str]:
    """Every defect flag any source raised, in first-seen order.

    Non-defect flags (`author_unknown`, `authors_swapped` from a sparse record)
    are taken from the best match only — they describe *that* comparison, not
    the reference.
    """
    best_name, best = max(scored, key=lambda item: item[1]["score"])
    flags: list[str] = list(best["flags"])
    for name, match in scored:
        if name == best_name:
            continue
        for flag in match["flags"]:
            if flag in _PROBLEMATIC_FLAGS and flag not in flags:
                flags.append(flag)
    return flags


def _mismatch_lines_across_sources(scored: list[tuple[str, MatchScore]]) -> list[str]:
    best_name, best = max(scored, key=lambda item: item[1]["score"])
    lines = list(_mismatch_lines(best))
    for name, match in scored:
        if name == best_name:
            continue
        if not any(flag in _PROBLEMATIC_FLAGS for flag in match["flags"]):
            continue
        for line in _mismatch_lines(match):
            annotated = f"{line} (per {name})"
            if annotated not in lines and line not in lines:
                lines.append(annotated)
    return lines


def _mismatch_lines(match: MatchScore) -> list[str]:
    lines: list[str] = []
    if "title_mismatch" in match["flags"]:
        lines.append(f"title similarity only {match['title_similarity']}")
    if "author_mismatch" in match["flags"] or "chimeric" in match["flags"]:
        lines.append(f"author agreement only {match['author_similarity']}")
    if "author_unknown" in match["flags"]:
        lines.append("matched record carries no author list to compare")
    if "venue_mismatch" in match["flags"]:
        lines.append("venue disagrees with the matched record")
    if "fabricated_author" in match["flags"]:
        lines.append("entry lists authors absent from the matched record")
    if "authors_swapped" in match["flags"]:
        lines.append("authors appear in a different order than published")
    if "author_truncated" in match["flags"]:
        lines.append("author list is truncated without an 'and others' sentinel")
    if "doi_mismatch" in match["flags"]:
        lines.append("DOI disagrees with the matched record")
    if "given_name_substitution" in match["flags"]:
        lines.append("an author's first name differs from the matched record")
    return lines


def check_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    strict: bool = False,
    fetch_crossref: Callable[..., NormalizedRecord | None] | None = None,
    fetch_openalex: Callable[..., NormalizedRecord | None] | None = None,
    fetch_dblp: Callable[..., NormalizedRecord | None] | None = None,
    fetch_openreview: Callable[..., NormalizedRecord | None] | None = None,
    fetch_s2: Callable[..., NormalizedRecord | None] | None = None,
    now_year: int | None = None,
) -> CheckResult:
    """Validate every entry in a library against authoritative metadata sources."""
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return _error_result(strict, resolved.errors)

    config, bib = resolved
    s2_api_key = resolve_optional_value(
        command=config.get("semantic_scholar_api_key_cmd"),
        fallback=config.get("semantic_scholar_api_key"),
    )
    contact_email = resolve_contact_email(config)
    fetch_text = build_metadata_fetch_text(config, api_key=s2_api_key)
    providers = _providers(
        fetch_text=fetch_text,
        s2_api_key=s2_api_key,
        contact_email=contact_email,
        overrides={
            "crossref": fetch_crossref,
            "openalex": fetch_openalex,
            "dblp": fetch_dblp,
            "openreview": fetch_openreview,
            "s2": fetch_s2,
        },
    )

    # `read_bib_file` drops what the lenient parser could not turn into an
    # entry — including a *duplicate citekey*, where the second block is simply
    # not read. A 3-entry bib with one fabricated duplicate audited as
    # `total: 1, verified: 1, problematic: 0, status: "ok"`, exit 0: an
    # unaudited entry inside a clean bill of health. An audit tool cannot report
    # on a file it only partly read.
    read_result, dropped_blocks = read_bib_file_with_failures(bib["path"])
    records = read_result["records"]
    effective_year = now_year if now_year is not None else time.gmtime().tm_year
    counts = {"verified": 0, "could_not_verify": 0, "problematic": 0}
    items: list[CheckItem] = []
    for record in records:
        if not isinstance(record.get("citekey"), str):
            continue
        item = _verify_entry(record, providers, strict=strict, now_year=effective_year)
        counts[item["verdict"]] += 1
        items.append(item)

    # Summarize per-source failures once for the run rather than repeating the
    # same "connection refused" under every entry. A `check` that reached no
    # source has audited nothing, and must not read as a clean bill of health.
    failed_sources = sorted({
        error.split(":", 1)[0]
        for item in items
        for error in item.get("source_errors", [])
    })
    run_errors = [
        f"{name}: unreachable for some or all entries" for name in failed_sources
    ]
    run_errors.extend(
        f"not audited: {message}" for message in dropped_blocks
    )

    return {
        # A run that could not read part of the file has not audited it, and
        # `ok` would be read as "everything here is fine".
        "status": "ok" if not dropped_blocks else "error",
        "bib_name": bib["name"],
        "strict": strict,
        "total": len(items),
        "counts": counts,
        "items": items,
        "errors": run_errors,
    }


def _error_result(strict: bool, errors: list[str]) -> CheckResult:
    return {
        "status": "error",
        "bib_name": None,
        "strict": strict,
        "total": 0,
        "counts": {"verified": 0, "could_not_verify": 0, "problematic": 0},
        "items": [],
        "errors": errors,
    }
