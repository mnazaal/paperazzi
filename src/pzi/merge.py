"""Pure helpers for conservative entry enrichment."""

from __future__ import annotations

from typing import Any, TypeAlias, cast

MergeableEntry: TypeAlias = dict[str, Any]



MergeDecision: TypeAlias = dict[str, Any]



USER_OWNED_FIELDS = frozenset({"tags", "citekey"})
PREFER_LONGER_TEXT_FIELDS = frozenset({"title", "venue", "note", "abstract"})
FILL_IF_MISSING_FIELDS = frozenset(
    {
        "doi",
        "arxiv_id",
        "canonical_url",
        "source_url",
        "pdf_url",
        "abstract_url",
        "local_pdf_path",
        "source_name",
        "source_payload",
    }
)


def merge_entries(existing: MergeableEntry, incoming: MergeableEntry) -> MergeDecision:
    """Merge an incoming record into an existing entry conservatively."""
    merged = cast(MergeableEntry, dict(existing))
    changed_fields: list[str] = []

    existing_tags = existing.get("tags") or []
    incoming_tags = incoming.get("tags") or []
    merged_tags = sorted({*existing_tags, *incoming_tags})
    if merged_tags != existing_tags:
        merged["tags"] = merged_tags
        changed_fields.append("tags")
    elif existing.get("tags") is not None:
        merged["tags"] = existing["tags"]

    existing_authors = existing.get("authors") or []
    incoming_authors = incoming.get("authors") or []
    merged_authors = (
        incoming_authors
        if len(incoming_authors) > len(existing_authors)
        else existing_authors
    )
    if merged_authors != existing_authors:
        merged["authors"] = merged_authors
        changed_fields.append("authors")

    existing_year = existing.get("year")
    incoming_year = incoming.get("year")
    merged_year = existing_year if existing_year is not None else incoming_year
    if merged_year != existing_year:
        merged["year"] = merged_year
        changed_fields.append("year")

    for field in PREFER_LONGER_TEXT_FIELDS:
        current_value = existing.get(field)
        incoming_value = incoming.get(field)
        merged_value = _prefer_more_informative_text(current_value, incoming_value)
        if merged_value != current_value:
            merged[field] = merged_value
            changed_fields.append(field)

    for field in FILL_IF_MISSING_FIELDS:
        current_value = existing.get(field)
        incoming_value = incoming.get(field)
        has_current = False
        if current_value is not None:
            if isinstance(current_value, str):
                has_current = bool(current_value.strip())
            elif isinstance(current_value, list):
                has_current = bool(current_value)
            else:
                has_current = True
        merged_value = current_value if has_current else incoming_value
        if merged_value != current_value:
            merged[field] = merged_value
            changed_fields.append(field)

    for field in USER_OWNED_FIELDS:
        if field not in merged and field in existing:
            merged[field] = existing[field]

    return {
        "merged": merged,
        "changed_fields": sorted(set(changed_fields)),
    }


def _prefer_more_informative_text(
    existing: str | None, incoming: str | None
) -> str | None:
    if existing is None or (isinstance(existing, str) and not existing.strip()):
        return incoming
    if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
        return existing
    assert isinstance(existing, str)
    assert isinstance(incoming, str)
    return incoming if len(incoming.strip()) > len(existing.strip()) else existing
