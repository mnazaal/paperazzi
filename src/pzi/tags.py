"""Pure helpers for normalizing entry-local tags."""

from __future__ import annotations

import re
import unicodedata

TAG_SEPARATOR_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize_tag(value: str) -> str | None:
    """Normalize a user tag into a lowercase slug, or None if empty."""
    ascii_value = _to_ascii(value)
    lowered = ascii_value.lower().strip()
    collapsed = TAG_SEPARATOR_PATTERN.sub("-", lowered).strip("-")
    return collapsed or None


def normalize_tags(values: list[str]) -> list[str]:
    """Normalize, deduplicate, and sort tags for stable storage."""
    normalized_values = [normalize_tag(value) for value in values]
    unique_values = {value for value in normalized_values if value is not None}
    return sorted(unique_values)


def parse_tag_csv(value: str) -> list[str]:
    """Parse a comma-separated tag string using the shared normalization rules."""
    return normalize_tags(value.split(","))


def _to_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")
