"""Unified full-text and field-filtered search service."""

from __future__ import annotations

from typing import NotRequired, TypedDict, cast

from pzi.bib_repository import read_bib_file_with_notices
from pzi.bib_service import resolve_sort_field, sort_records
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
    #: How many entries matched before `offset`/`limit` were applied. `matches`
    #: is the page; this is the answer to "how many are there". Without it a
    #: paged caller cannot tell a last page from a truncated one.
    total: int
    offset: int
    #: The page size in force, or `None` for "all of them". Unlike `entries`,
    #: search does not default to a page (decision 42): a default cap would
    #: silently redefine every existing `pzi search ... | wc -l`.
    limit: int | None
    sort: str
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
    venue: str | None = None,
    doi: str | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> SearchResult:
    """Search a bib with combined filters (AND logic).

    At least one filter must be provided.  Matches are case-insensitive.

    *sort*, *offset* and *limit* page the results the same way `list_entries`
    pages a listing, through the same `sort_records` — a common term on a 22k
    library matched 8029 entries and printed all of them in citekey order.
    *limit* defaults to `None`, meaning all of them, so no existing invocation
    changes meaning (decision 42); `entries` defaults to a page because it has
    always done so.
    """
    if all(f is None for f in (query, author, year, tag, venue, doi)):
        return _failed_search(
            bib_name=None,
            errors=["provide at least one of --query, --author, --year, --tag, --venue, --doi"],
            reason=REASON_USAGE,
            sort=sort, offset=offset, limit=limit,
        )

    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return _failed_search(
            bib_name=None,
            errors=resolved.errors,
            reason=REASON_CONFIG,
            sort=sort, offset=offset, limit=limit,
        )
    _config, bib = resolved

    normalized_tag = None
    if tag is not None:
        tag_norm = normalize_tags([tag])
        if not tag_norm:
            # `--tag "!!"` normalizes to nothing. Falling through with
            # `normalized_tag = None` dropped the filter entirely and returned
            # *every* entry — the opposite of a filter that matched nothing.
            return _failed_search(
                bib_name=bib["name"],
                errors=[f"tag {tag!r} contains no searchable characters"],
                reason=REASON_USAGE,
                sort=sort, offset=offset, limit=limit,
            )
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
            venue=venue,
            doi=doi,
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

    # Sorted through the same `sort_records` as `list_entries`, so `--sort year`
    # means the same thing in both commands. A `SearchMatch` carries the fields
    # the sort reads, so it is its own record here.
    sort_field = resolve_sort_field(sort)
    matches = sort_records(matches, sort_field)
    total = len(matches)
    page = matches[offset:] if limit is None else matches[offset : offset + limit]
    return {
        "status": "ok",
        "bib_name": bib["name"],
        "matches": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort_field,
        "errors": [],
        "warnings": dropped,
    }


def _failed_search(
    *,
    bib_name: str | None,
    errors: list[str],
    reason: str,
    sort: str | None,
    offset: int,
    limit: int | None,
) -> SearchResult:
    """A refusal, carrying the paging keys a successful result would have.

    `SearchResult` is a total TypedDict, so the three failure paths must fill
    them too rather than leave a caller guessing whether a missing `total`
    means zero. One constructor, so a key added above cannot be filled on the
    success path and forgotten on all three of these.
    """
    return {
        "status": "error",
        "bib_name": bib_name,
        "matches": [],
        "total": 0,
        "offset": offset,
        "limit": limit,
        "sort": resolve_sort_field(sort),
        "errors": errors,
        "reason": reason,
    }


def _match_record(
    record: dict[str, object],
    *,
    query: str | None,
    author: str | None,
    year: int | None,
    tag: str | None,
    venue: str | None = None,
    doi: str | None = None,
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

    if venue is not None:
        # `journal` and `booktitle` are the same question asked of an article
        # and of a proceedings paper, so one flag searches both rather than
        # making the user know which kind of entry they are looking for.
        venue_lower = venue.lower()
        hit = False
        for field in ("journal", "booktitle", "venue"):
            value = record.get(field)
            if isinstance(value, str) and venue_lower in value.lower():
                matched.append(field)
                hit = True
        if not hit:
            return None

    if doi is not None:
        # Substring, like every other filter here, so a bare suffix finds the
        # entry — but case-folded, because DOIs are case-insensitive by spec and
        # a library mixes the casings its sources happened to use.
        value = record.get("doi")
        if isinstance(value, str) and doi.lower() in value.lower():
            matched.append("doi")
        else:
            return None

    return matched
