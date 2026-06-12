"""Unified full-text and field-filtered search service."""

from __future__ import annotations

from typing import Any, TypeAlias, cast

from pzi.bib_repository import read_bib_file
from pzi.bibtex import normalize_authors
from pzi.config import load_and_resolve_bib
from pzi.tag_service import normalize_tags

SearchMatch: TypeAlias = dict[str, Any]



SearchResult: TypeAlias = dict[str, Any]



def search_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    query: str | None = None,
    author: str | None = None,
    year: int | None = None,
    tag: str | None = None,
) -> SearchResult:
    """Search a bib with combined filters (AND logic).

    At least one filter must be provided.  Matches are case-insensitive.
    """
    resolved = load_and_resolve_bib(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, list):
        return {
            "status": "error",
            "bib_name": None,
            "matches": [],
            "errors": resolved,
        }
    _config, bib = resolved

    normalized_tag = None
    if tag is not None:
        tag_norm = normalize_tags([tag])
        if tag_norm:
            normalized_tag = tag_norm[0]

    records = read_bib_file(bib["path"])["records"]
    matches: list[SearchMatch] = []
    for record in records:
        citekey = record.get("citekey")
        if not isinstance(citekey, str):
            continue  # pragma: no cover — covered by integration/browser tests

        match_result = _match_record(
            record,
            query=query,
            author=author,
            year=year,
            tag=normalized_tag,
        )
        if match_result is not None:
            matches.append(
                {
                    "citekey": citekey,
                    "title": cast(str | None, record.get("title")),
                    "authors": normalize_authors(record.get("authors")),
                    "year": cast(int | None, record.get("year")),
                    "tags": list(record.get("tags") or []),
                    "matched_fields": match_result,
                }
            )

    matches.sort(key=lambda m: m["citekey"])
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "matches": matches,
        "errors": [],
    }


def _match_record(
    record: dict[str, object],
    *,
    query: str | None,
    author: str | None,
    year: int | None,
    tag: str | None,
) -> list[str] | None:
    """Return matched field names if all active filters match, else None."""
    matched: list[str] = []

    if query is not None:
        query_lower = query.lower()
        found = False
        for field in ("title", "abstract", "note"):
            value = record.get(field)
            if isinstance(value, str) and query_lower in value.lower():
                matched.append(field)
                found = True
        if not found:
            return None

    if author is not None:
        author_lower = author.lower()
        authors = record.get("authors")
        if isinstance(authors, list) and any(
            isinstance(a, str) and author_lower in a.lower() for a in authors
        ):
            matched.append("authors")
        else:
            return None

    if year is not None:
        if record.get("year") == year:
            matched.append("year")
        else:
            return None

    if tag is not None:
        tags = record.get("tags")
        if isinstance(tags, list) and tag in tags:
            matched.append("tags")
        else:
            return None

    return matched
