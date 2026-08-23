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

from pzi.identifiers import detect_preprint_source
from pzi.similarity import (
    author_surnames,
    authors_swapped,
    canonical_doi,
    classify_given_pair,
    has_truncation_sentinel,
    is_alphabetized_record,
    is_truncation_sentinel,
    jaccard_similarity,
    levenshtein_within_1,
    normalize_title,
    split_family_given_folded,
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
# Higher than venue: a DOI is an exact identifier, so two different ones are a
# contradiction rather than a naming difference.
_PENALTY_DOI = 25
# A wrong year is one of the commonest fingerprints of a hallucinated citation,
# but online-first and print years legitimately differ by one, so only a gap
# wider than that counts.
_PENALTY_YEAR = 20
_YEAR_TOLERANCE = 1
_PENALTY_GIVEN_SUB = 20
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


def title_similarity_score(a: str | None, b: str | None) -> int:
    return round(jaccard_similarity(title_tokens(a), title_tokens(b)) * 100)


def _author_similarity(entry: Sequence[str], candidate: Sequence[str]) -> int:
    """How much of the *entry's* author list the candidate confirms.

    Containment, not symmetric Jaccard. The question is "does the source back
    up what the entry claims", which is asymmetric: an entry listing 2 of a
    paper's 4 authors — an ordinary abbreviated citation — scored 50 under
    Jaccard and was reported `problematic`, which under `--strict` fails CI on
    a correct bibliography. Authors the entry claims and the source does not
    have still lower the score, and `_fabricated_surnames` flags them
    separately.
    """
    a, b = set(author_surnames(entry)), set(author_surnames(candidate))
    if not a or not b:
        return 0
    return round(len(a & b) / len(a) * 100)


def _fabricated_surnames(entry: Sequence[str], candidate: Sequence[str]) -> list[str]:
    """Entry surnames absent from the candidate (possible fabricated authors)."""
    cand = set(author_surnames(candidate))
    return [s for s in author_surnames(entry) if s not in cand]


#: DOI prefixes belonging to preprint servers, where a different DOI on the
#: published record is expected rather than contradictory.
_PREPRINT_DOI_PREFIXES = (
    "10.48550",  # arXiv
    "10.1101",   # bioRxiv / medRxiv
    "10.21203",  # Research Square
    "10.2139",   # SSRN
    "10.31234",  # PsyArXiv
    "10.31219",  # OSF Preprints
)


def _doi_mismatch(entry: Mapping[str, object], candidate: Mapping[str, object]) -> bool:
    """True only when both records carry a DOI and the two disagree.

    An absent DOI on either side is absent evidence, not disagreement — most
    provider records are sparse, so treating a missing DOI as a contradiction
    would flag almost everything.

    A preprint entry is excluded: its DOI legitimately differs from the
    published version's, and `promote_planning` scores exactly that pairing with
    this function. Penalizing it there would suppress valid promotions, which is
    the failure mode this guard exists to prevent.
    """
    e = canonical_doi(entry.get("doi"))
    c = canonical_doi(candidate.get("doi"))
    if e is None or c is None or e == c:
        return False
    if detect_preprint_source(entry) is not None:
        return False
    return not e.startswith(_PREPRINT_DOI_PREFIXES)


def _given_name_substitutions(
    entry: Sequence[str], candidate: Sequence[str]
) -> list[str]:
    """Surnames where the entry and candidate give genuinely different first names.

    Author comparison elsewhere is surname-only (`_normalize_author` discards
    the given name), so "Shunyu Yao" vs "Denny Zhou" scores identically to an
    exact match whenever the surname agrees. Pairing by surname and comparing
    the given names recovers that signal, which is a common fingerprint of a
    fabricated citation.
    """
    by_surname: dict[str, str] = {}
    for name in candidate:
        family, given = split_family_given_folded(name)
        if family:
            by_surname.setdefault(family, given)

    substituted: list[str] = []
    for name in entry:
        family, given = split_family_given_folded(name)
        cand_given = by_surname.get(family)
        if cand_given is None:
            continue  # unmatched surname is _fabricated_surnames' business
        if classify_given_pair(given, cand_given) == "substitution":
            substituted.append(family)
    return substituted


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
    title_sim = title_similarity_score(
        _str_field(entry, "title"), _str_field(candidate, "title")
    )
    author_sim = _author_similarity(entry_authors, cand_authors)
    # A side with no author list is *absent evidence*, not disagreement, but
    # `_author_similarity` scores both 0. Penalizing it rejects sparse-but-valid
    # provider records (some return title + venue only) on a perfect title
    # match, which is a worse failure than the mismatch it would catch.
    author_evidence = bool(author_surnames(entry_authors)) and bool(
        author_surnames(cand_authors)
    )

    flags: list[str] = []
    contributions: list[str] = [
        f"title similarity {title_sim}",
        f"author similarity {author_sim}" if author_evidence else "no author data to compare",
    ]
    if not author_evidence:
        flags.append("author_unknown")

    # Chimeric case: a strong title but weak authors is the classic swapped /
    # fabricated-author citation — score it down asymmetrically.
    if author_evidence and title_sim >= _TITLE_HIGH and author_sim < _AUTHOR_OK:
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
        if author_evidence and author_sim < _AUTHOR_OK:
            score -= _PENALTY_AUTHOR
            flags.append("author_mismatch")
            contributions.append(f"author mismatch -{_PENALTY_AUTHOR}")

    if _venue_mismatch(entry, candidate):
        score -= _PENALTY_VENUE
        flags.append("venue_mismatch")
        contributions.append(f"venue mismatch -{_PENALTY_VENUE}")

    if _doi_mismatch(entry, candidate):
        score -= _PENALTY_DOI
        flags.append("doi_mismatch")
        contributions.append(f"DOI disagrees with the matched record -{_PENALTY_DOI}")

    year_gap = _year_gap(entry, candidate)
    if year_gap is not None and year_gap > _YEAR_TOLERANCE:
        score -= _PENALTY_YEAR
        flags.append("year_mismatch")
        contributions.append(
            f"year disagrees with the matched record by {year_gap} -{_PENALTY_YEAR}"
        )

    fabricated = _fabricated_surnames(entry_authors, cand_authors) if author_evidence else []
    if len(fabricated) >= 2:
        penalty = min(len(fabricated) * _PENALTY_FAB_EACH, _PENALTY_FAB_CAP)
        score -= penalty
        flags.append("fabricated_author")
        contributions.append(f"{len(fabricated)} unmatched author(s) -{penalty}")

    substituted = (
        _given_name_substitutions(entry_authors, cand_authors) if author_evidence else []
    )
    if substituted:
        score -= _PENALTY_GIVEN_SUB
        flags.append("given_name_substitution")
        contributions.append(
            f"different first name for {', '.join(substituted)} -{_PENALTY_GIVEN_SUB}"
        )

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


def _year(record: Mapping[str, object]) -> int | None:
    value = record.get("year")
    if isinstance(value, bool):  # `bool` is an `int` subclass
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _year_gap(entry: Mapping[str, object], candidate: Mapping[str, object]) -> int | None:
    """How far apart the two years are, or None when either side has none.

    The module docstring and `README.md` have always advertised a title/author/
    **year** mismatch check; nothing ever compared the year, so an entry
    claiming `year = {1999}` for a 2017 paper scored `verified, confidence 100,
    flags: []` in both strict and loose mode.
    """
    e, c = _year(entry), _year(candidate)
    if e is None or c is None:
        return None
    return abs(e - c)


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
