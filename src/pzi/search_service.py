"""Unified full-text and field-filtered search service."""

from __future__ import annotations

from typing import NotRequired, TypedDict, cast

from pzi.bib_repository import read_bib_file_with_notices
from pzi.bibtex import normalize_authors
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.errors import REASON_CONFIG, REASON_USAGE
from pzi.tag_service import normalize_tags


class SearchMatch(TypedDict):
    """One search hit — what `pzi.search()` returns per match.

    `matched_fields` names which filters this entry matched, so a caller can
    tell a title hit from a tag hit.
    """

    citekey: str
    title: str | None
    authors: list[str]
    year: int | None
    tags: list[str]
    matched_fields: list[str]


class SearchResult(TypedDict):
    status: str
    bib_name: str | None
    matches: list[SearchMatch]
    errors: list[str]
    #: Blocks the parser dropped (e.g. a duplicate citekey). Non-fatal: the
    #: command succeeded and is reporting what it could read. Absent on the
    #: error paths, which never got as far as parsing.
    warnings: NotRequired[list[str]]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
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
    if query is None and author is None and year is None and tag is None:
        return {
            "status": "error",
            "bib_name": None,
            "matches": [],
            "errors": ["provide at least one of --query, --author, --year, --tag"],
            "reason": REASON_USAGE,
        }

    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "bib_name": None,
            "matches": [],
            "errors": resolved.errors,
            "reason": REASON_CONFIG,
        }
    _config, bib = resolved

    normalized_tag = None
    if tag is not None:
        tag_norm = normalize_tags([tag])
        if not tag_norm:
            # `--tag "!!"` normalizes to nothing. Falling through with
            # `normalized_tag = None` dropped the filter entirely and returned
            # *every* entry — the opposite of a filter that matched nothing.
            return {
                "status": "error",
                "bib_name": bib["name"],
                "matches": [],
                "errors": [f"tag {tag!r} contains no searchable characters"],
                "reason": REASON_USAGE,
            }
        normalized_tag = tag_norm[0]

    read_result, dropped = read_bib_file_with_notices(bib["path"])
    records = read_result["records"]
    matches: list[SearchMatch] = []
    for record in records:
        citekey = record.get("citekey")
        if not isinstance(citekey, str):
            continue

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
        "warnings": dropped,
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
        # Normalize both sides. The query was normalized and the stored tags
        # were not, so a library tag written as "Machine Learning" could never
        # be found by the normalized form the search had produced.
        stored = normalize_tags(tags) if isinstance(tags, list) else []
        if tag in stored:
            matched.append("tags")
        else:
            return None

    return matched
