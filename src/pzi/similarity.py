"""Pure deduplication helpers: exact-identity matching + fuzzy similarity hints."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Literal, TypeAlias

from pzi.bibtex import NormalizedRecord, normalize_authors

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SimilarityCandidate: TypeAlias = dict[str, Any]

IdentityKind = Literal["doi", "arxiv", "url"]

Identity: TypeAlias = dict[str, Any]

MatchableRecord = NormalizedRecord

# ---------------------------------------------------------------------------
# Exact-identity matching
# ---------------------------------------------------------------------------


def extract_identities(record: MatchableRecord) -> list[Identity]:
    """Extract exact-match identities from one normalized record."""
    candidates: list[tuple[IdentityKind, str | None]] = [
        ("doi", record.get("doi")),
        ("arxiv", record.get("arxiv_id")),
        ("url", record.get("canonical_url")),
    ]

    identities: list[Identity] = [
        {"kind": kind, "value": value}
        for kind, value in candidates
        if isinstance(value, str) and value.strip()
    ]
    return _deduplicate_identities(identities)


def build_identity_index(
    records: Sequence[MatchableRecord],
) -> dict[tuple[IdentityKind, str], list[int]]:
    """Index records by exact identity, preserving input positions."""
    index: dict[tuple[IdentityKind, str], list[int]] = {}
    for position, record in enumerate(records):
        for identity in extract_identities(record):
            key = (identity["kind"], identity["value"])
            index.setdefault(key, []).append(position)
    return index


def find_exact_match(
    record: MatchableRecord,
    existing_records: Sequence[MatchableRecord],
    *,
    index: dict[tuple[IdentityKind, str], list[int]] | None = None,
) -> int | None:
    """Return the first exact-match record position, or None when absent.

    Pass a prebuilt *index* (from :func:`build_identity_index`) to avoid
    rebuilding it on every call when matching repeatedly against the same
    ``existing_records`` — the add/capture write path does several lookups per
    entry.  When omitted, the index is built from *existing_records*.
    """
    identity_index = build_identity_index(existing_records) if index is None else index
    for identity in extract_identities(record):
        matches = identity_index.get((identity["kind"], identity["value"]))
        if matches:
            return matches[0]
    return None


def _deduplicate_identities(identities: list[Identity]) -> list[Identity]:
    seen: set[tuple[IdentityKind, str]] = set()
    deduplicated: list[Identity] = []
    for identity in identities:
        key = (identity["kind"], identity["value"])
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(identity)
    return deduplicated


# ---------------------------------------------------------------------------
# Fuzzy similarity
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str | None) -> str:
    if title is None:
        return ""
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    )
    return _NON_ALNUM.sub(" ", ascii_title.lower()).strip()


def title_tokens(title: str | None) -> set[str]:
    return {token for token in normalize_title(title).split() if len(token) > 2}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _normalize_author(name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    if "," in ascii_name:
        family = ascii_name.split(",", 1)[0]
    else:
        parts = ascii_name.split()
        family = parts[-1] if parts else ""
    return _NON_ALNUM.sub("", family.lower())


def author_overlap(a: list[str], b: list[str]) -> int:
    norm_a = {_normalize_author(x) for x in a}
    norm_b = {_normalize_author(x) for x in b}
    norm_a.discard("")
    norm_b.discard("")
    return len(norm_a & norm_b)


def compute_similarity_hint(
    record: SimilarityCandidate,
    existing_records: Sequence[SimilarityCandidate],
    *,
    title_threshold: float = 0.6,
    year_window: int = 2,
) -> str | None:
    """Return the citekey of the most similar existing record, if any."""
    record_tokens = title_tokens(record.get("title"))
    if not record_tokens:
        return None

    record_authors = normalize_authors(record.get("authors"))
    record_year = record.get("year")

    best_key: str | None = None
    best_score: float = 0.0
    for existing in existing_records:
        citekey = existing.get("citekey")
        if not isinstance(citekey, str) or not citekey.strip():
            continue
        existing_tokens = title_tokens(existing.get("title"))
        similarity = jaccard_similarity(record_tokens, existing_tokens)
        if similarity < title_threshold:
            continue

        existing_year = existing.get("year")
        if (
            record_year is not None
            and existing_year is not None
            and abs(record_year - existing_year) > year_window
        ):
            continue

        overlap = author_overlap(record_authors, list(existing.get("authors") or []))
        if overlap == 0 and similarity < 0.85:
            continue  # pragma: no cover — covered by integration/browser tests

        score = similarity + 0.1 * overlap
        if score > best_score:  # pragma: no branch — covered by integration/browser tests
            best_score = score
            best_key = citekey

    return best_key
