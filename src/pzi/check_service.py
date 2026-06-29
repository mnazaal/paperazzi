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
from typing import Literal, TypedDict

from pzi.bib_repository import read_bib_file
from pzi.bibtex import NormalizedRecord
from pzi.capture_context import resolve_contact_email, resolve_optional_value
from pzi.config import load_and_resolve_bib
from pzi.fetch_helpers import build_metadata_fetch_text
from pzi.metadata_sources import (
    fetch_crossref_record_by_title,
    fetch_dblp_record_by_title,
    fetch_openalex_record_by_title,
    fetch_openreview_record_by_title,
    fetch_semantic_scholar_record_by_title,
)
from pzi.resolution_match import MatchScore, score_match

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
        "doi_mismatch",
    }
)


class CheckItem(TypedDict):
    citekey: str
    verdict: Verdict
    confidence_score: int
    flags: list[str]
    mismatches: list[str]
    sources_checked: list[str]


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
_Provider = tuple[str, Callable[..., NormalizedRecord | None]]


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
            return (name, override)
        return (
            name,
            lambda title: base(title, contact_email=contact_email, fetch_text=fetch_text),
        )

    s2_override = overrides.get("s2")
    s2_fn = s2_override or (
        lambda title: fetch_semantic_scholar_record_by_title(
            title, api_key=s2_api_key, fetch_text=fetch_text
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
    scored: list[tuple[str, MatchScore]] = []
    for name, fetch in providers:
        try:
            candidate = fetch(title)
        except Exception:
            continue
        sources_checked.append(name)
        if candidate is None:
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
    )


def _verdict_from_scores(
    citekey: str,
    scored: list[tuple[str, MatchScore]],
    sources_checked: list[str],
    *,
    strict: bool,
    base_flags: list[str],
    base_mismatches: list[str],
) -> CheckItem:
    if not scored:
        # Nothing matched anywhere: abstain (not a clean pass), unless a base
        # defect (impossible year) already condemns it.
        return {
            "citekey": citekey,
            "verdict": "problematic" if base_flags else "could_not_verify",
            "confidence_score": 0,
            "flags": base_flags,
            "mismatches": base_mismatches or ["no source could confirm this reference"],
            "sources_checked": sources_checked,
        }

    best_name, best = max(scored, key=lambda item: item[1]["score"])
    flags = [*base_flags, *best["flags"]]
    mismatches = [*base_mismatches, *_mismatch_lines(best)]
    bar = _VERIFIED_BAR_STRICT if strict else _VERIFIED_BAR

    if any(f in _PROBLEMATIC_FLAGS for f in flags):
        verdict: Verdict = "problematic"
    elif best["score"] >= bar:
        verdict = "verified"
    else:
        verdict = "could_not_verify"

    contributions = [f"best match via {best_name}: {c}" for c in best["contributions"]]
    return {
        "citekey": citekey,
        "verdict": verdict,
        "confidence_score": best["score"],
        "flags": flags,
        "mismatches": mismatches or contributions,
        "sources_checked": sources_checked,
    }


def _mismatch_lines(match: MatchScore) -> list[str]:
    lines: list[str] = []
    if "title_mismatch" in match["flags"]:
        lines.append(f"title similarity only {match['title_similarity']}")
    if "author_mismatch" in match["flags"] or "chimeric" in match["flags"]:
        lines.append(f"author agreement only {match['author_similarity']}")
    if "venue_mismatch" in match["flags"]:
        lines.append("venue disagrees with the matched record")
    if "fabricated_author" in match["flags"]:
        lines.append("entry lists authors absent from the matched record")
    if "authors_swapped" in match["flags"]:
        lines.append("authors appear in a different order than published")
    if "author_truncated" in match["flags"]:
        lines.append("author list is truncated without an 'and others' sentinel")
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
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return _error_result(strict, resolved)

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

    records = read_bib_file(bib["path"])["records"]
    effective_year = now_year if now_year is not None else time.gmtime().tm_year
    counts = {"verified": 0, "could_not_verify": 0, "problematic": 0}
    items: list[CheckItem] = []
    for record in records:
        if not isinstance(record.get("citekey"), str):
            continue
        item = _verify_entry(record, providers, strict=strict, now_year=effective_year)
        counts[item["verdict"]] += 1
        items.append(item)

    return {
        "status": "ok",
        "bib_name": bib["name"],
        "strict": strict,
        "total": len(items),
        "counts": counts,
        "items": items,
        "errors": [],
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
