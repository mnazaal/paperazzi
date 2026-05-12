"""Pure helpers for exact-identity matching."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, TypeAlias

IdentityKind = Literal["doi", "arxiv", "url"]


Identity: TypeAlias = dict[str, Any]



MatchableRecord: TypeAlias = dict[str, Any]



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
    record: MatchableRecord, existing_records: Sequence[MatchableRecord]
) -> int | None:
    """Return the first exact-match record position, or None when absent."""
    index = build_identity_index(existing_records)
    for identity in extract_identities(record):
        matches = index.get((identity["kind"], identity["value"]))
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
