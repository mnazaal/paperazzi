"""Shared 0–100 confidence scoring for resolution matches.

Used by both preprint promotion (`promote_service`) and reference validation
(`check_service`) to compare a library entry against a candidate record fetched
from an authoritative source.  Produces an explainable breakdown — per-field
similarity, explicit penalty/bonus contributions, and defect flags — rather than
a single opaque number, so users can see *why* a match was accepted or rejected.

Pure functions over `NormalizedRecord`-shaped mappings; reuses the title/author
primitives in :mod:`pzi.similarity`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict

from pzi.similarity import (
    author_surnames,
    authors_swapped,
    has_truncation_sentinel,
    is_alphabetized_record,
    is_truncation_sentinel,
    jaccard_similarity,
    levenshtein_within_1,
    normalize_title,
    title_tokens,
)

# Thresholds (0–100 field-similarity space).
_TITLE_OK = 60
_AUTHOR_OK = 60
_TITLE_HIGH = 85  # title strong enough to anchor a chimeric check

# Penalty / bonus magnitudes (match bibtexupdater's documented weights).
_PENALTY_TITLE = 20
_PENALTY_AUTHOR = 20
_PENALTY_VENUE = 15
_PENALTY_FAB_EACH = 10
_PENALTY_FAB_CAP = 20
_BONUS_MULTI_SOURCE = 10


class MatchScore(TypedDict):
    score: int               # 0–100 overall confidence
    title_similarity: int    # 0–100
    author_similarity: int   # 0–100
    flags: list[str]         # defect markers: title_mismatch, author_mismatch, …
    contributions: list[str] # human-readable breakdown lines


def _authors(record: Mapping[str, object]) -> list[str]:
    raw = record.get("authors")
    return [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []


def _str_field(record: Mapping[str, object], key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _title_similarity(a: str | None, b: str | None) -> int:
    return round(jaccard_similarity(title_tokens(a), title_tokens(b)) * 100)


def _author_similarity(entry: Sequence[str], candidate: Sequence[str]) -> int:
    a, b = set(author_surnames(entry)), set(author_surnames(candidate))
    if not a or not b:
        return 0
    return round(jaccard_similarity(a, b) * 100)


def _fabricated_surnames(entry: Sequence[str], candidate: Sequence[str]) -> list[str]:
    """Entry surnames absent from the candidate (possible fabricated authors)."""
    cand = set(author_surnames(candidate))
    return [s for s in author_surnames(entry) if s not in cand]


def score_match(
    entry: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    author_sources: int = 1,
    strict: bool = False,
) -> MatchScore:
    """Score how well *candidate* confirms *entry* on a 0–100 scale.

    ``author_sources`` is the number of order-reliable sources that agree on the
    candidate's authors; ≥2 grants a small confirmation bonus.  ``strict`` adds
    the high-stakes checks (single-edit title typos, silently truncated author
    lists) where the cost of a missed defect outweighs a false alarm.
    """
    entry_authors, cand_authors = _authors(entry), _authors(candidate)
    title_sim = _title_similarity(
        _str_field(entry, "title"), _str_field(candidate, "title")
    )
    author_sim = _author_similarity(entry_authors, cand_authors)

    flags: list[str] = []
    contributions: list[str] = [
        f"title similarity {title_sim}",
        f"author similarity {author_sim}",
    ]

    # Chimeric case: a strong title but weak authors is the classic swapped /
    # fabricated-author citation — score it down asymmetrically.
    if title_sim >= _TITLE_HIGH and author_sim < _AUTHOR_OK:
        score = round(title_sim - 0.5 * (100 - author_sim))
        flags.append("chimeric")
        flags.append("author_mismatch")
        contributions.append("chimeric: high title, low author agreement")
    else:
        score = title_sim
        if title_sim < _TITLE_OK:
            score -= _PENALTY_TITLE
            flags.append("title_mismatch")
            contributions.append(f"title mismatch -{_PENALTY_TITLE}")
        if author_sim < _AUTHOR_OK:
            score -= _PENALTY_AUTHOR
            flags.append("author_mismatch")
            contributions.append(f"author mismatch -{_PENALTY_AUTHOR}")

    if _venue_mismatch(entry, candidate):
        score -= _PENALTY_VENUE
        flags.append("venue_mismatch")
        contributions.append(f"venue mismatch -{_PENALTY_VENUE}")

    fabricated = _fabricated_surnames(entry_authors, cand_authors)
    if len(fabricated) >= 2:
        penalty = min(len(fabricated) * _PENALTY_FAB_EACH, _PENALTY_FAB_CAP)
        score -= penalty
        flags.append("fabricated_author")
        contributions.append(f"{len(fabricated)} unmatched author(s) -{penalty}")

    if authors_swapped(
        entry_authors,
        cand_authors,
        candidate_alphabetized=is_alphabetized_record(candidate.get("doi")),
    ):
        flags.append("authors_swapped")
        contributions.append("authors in different order")

    if author_sources >= 2 and author_sim >= _AUTHOR_OK:
        score += _BONUS_MULTI_SOURCE
        contributions.append(f"multi-source author confirmation +{_BONUS_MULTI_SOURCE}")

    if strict:
        score -= _apply_strict_checks(
            entry, candidate, entry_authors, cand_authors, flags, contributions
        )

    score = max(0, min(100, score))
    return {
        "score": score,
        "title_similarity": title_sim,
        "author_similarity": author_sim,
        "flags": flags,
        "contributions": contributions,
    }


def _apply_strict_checks(
    entry: Mapping[str, object],
    candidate: Mapping[str, object],
    entry_authors: list[str],
    cand_authors: list[str],
    flags: list[str],
    contributions: list[str],
) -> int:
    """Append strict-only defect flags; return the total confidence penalty.

    * Single-edit title typo: a normalized title within Levenshtein-1 of the
      matched record (but not equal) is the fingerprint of a fabricated near-miss
      citation that whole-token similarity misses.
    * Silent author truncation: an entry that lists fewer authors than the
      matched record without an ``and others`` / ``et al`` sentinel misrepresents
      authorship.
    """
    penalty = 0
    entry_title = normalize_title(_str_field(entry, "title"))
    cand_title = normalize_title(_str_field(candidate, "title"))
    if (
        entry_title
        and cand_title
        and entry_title != cand_title
        and levenshtein_within_1(entry_title, cand_title)
        and "title_mismatch" not in flags
    ):
        flags.append("title_mismatch")
        contributions.append("strict: title within one edit of the matched record")
        penalty += _PENALTY_TITLE

    named = len(author_surnames([a for a in entry_authors if not is_truncation_sentinel(a)]))
    cand_named = len(author_surnames(cand_authors))
    if (
        not has_truncation_sentinel(entry_authors)
        and 0 < named < cand_named
        and "author_truncated" not in flags
    ):
        flags.append("author_truncated")
        contributions.append(
            f"strict: lists {named} of {cand_named} authors with no 'and others'"
        )
        penalty += _PENALTY_AUTHOR
    return penalty


def _venue_mismatch(entry: Mapping[str, object], candidate: Mapping[str, object]) -> bool:
    """True only when both records name a venue and they clearly disagree."""
    e = entry.get("venue")
    c = candidate.get("venue")
    if not isinstance(e, str) or not isinstance(c, str) or not e.strip() or not c.strip():
        return False
    en, cn = normalize_title(e), normalize_title(c)
    if not en or not cn:
        return False
    if en == cn or en in cn or cn in en:
        return False
    return jaccard_similarity(set(en.split()), set(cn.split())) < 0.5
