"""Pure citekey generation helpers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, TypeAlias

CitekeyInput: TypeAlias = dict[str, Any]



NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
    }
)


def generate_citekey_base(data: CitekeyInput) -> str:
    """Generate a deterministic citekey base from author, year, and title."""
    author_part = _author_token(data["authors"])
    year_part = _year_token(data["year"])
    title_part = _title_token(data["title"])
    return f"{author_part}{year_part}{title_part}"


def resolve_citekey_collision(base: str, existing_keys: set[str]) -> str:
    """Return the first available citekey using a numeric suffix when needed."""
    if base not in existing_keys:
        return base

    suffix = 2
    while f"{base}{suffix}" in existing_keys:
        suffix += 1
    return f"{base}{suffix}"


def generate_citekey(data: CitekeyInput, existing_keys: set[str]) -> str:
    """Generate a citekey and resolve collisions against existing keys."""
    base = generate_citekey_base(data)
    return resolve_citekey_collision(base, existing_keys)


def _author_token(authors: list[str]) -> str:
    if not authors:
        return "unknown"

    first_author = authors[0].strip()
    if not first_author:
        return "unknown"

    if "," in first_author:
        family_name = first_author.split(",", 1)[0]
    else:
        family_name = first_author.split()[-1]

    token = _slug_token(family_name)
    return token or "unknown"


def _year_token(year: int | None) -> str:
    if year is None:
        return "xxxx"
    return str(year)


def _title_token(title: str | None) -> str:
    if title is None:
        return "untitled"

    words = [_slug_token(part) for part in title.split()]
    meaningful_words = [word for word in words if word and word not in STOPWORDS]
    if not meaningful_words:
        return "untitled"
    return meaningful_words[0]


def _slug_token(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    lowered = ascii_value.lower().strip()
    collapsed = NON_ALNUM_PATTERN.sub("", lowered)
    return collapsed
