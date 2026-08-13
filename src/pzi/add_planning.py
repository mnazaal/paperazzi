"""Add/capture planning and metadata fetching.

Two halves, deliberately named apart because they differ in kind:

- **Planning** (record merging, citekey choice, diagnostics, result shaping) is
  pure computation over its arguments.
- **Fetching** (``fetch_record_for_input``, ``build_discovery_context``) is the
  provider cascade: it makes network calls through injected fetchers, defaulting
  to the live Crossref/OpenAlex/Semantic Scholar ones.

The module docstring used to claim the whole thing was pure, so a test that
exercised "planning" could reach the network without saying so.
"""

from __future__ import annotations

import urllib.error
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast
from urllib.parse import urlsplit

from pzi.bibtex import NormalizedRecord
from pzi.capture_context import CaptureContext, build_capture_context
from pzi.config import AppConfig, BibConfig
from pzi.flaresolverr import fetch_html_via_flaresolverr
from pzi.html_metadata import extract_metadata_from_html
from pzi.metadata_sources import (
    fetch_crossref_record,
    fetch_openalex_record,
    fetch_semantic_scholar_record,
)
from pzi.pdf import NextPdfCandidate
from pzi.pdf_discovery import (
    DEFAULT_DISCOVERY_STEPS,
    PdfDiscoveryContext,
    apply_pdf_discovery,
    discovery_diagnostics,
)
from pzi.protocols import (
    HtmlFetcher,
    MetadataRecordFetcher,
    S2RecordFetcher,
    SearchTranslationFetcher,
    UnpaywallFetcher,
    WebTranslationFetcher,
    accepts_keyword,
)
from pzi.similarity import compute_similarity_hint, find_exact_match


def split_record_overrides(
    record_overrides: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    normal: dict[str, object] = {}
    fallback: dict[str, object] = {}
    for key, value in record_overrides.items():
        if key.startswith("fallback_"):
            fallback[key.removeprefix("fallback_")] = value
        else:
            normal[key] = value
    return normal, fallback


def merge_fetched_record_with_overrides(
    fetched_record: Mapping[str, object], record_overrides: Mapping[str, object]
) -> NormalizedRecord:
    normal, fallback = split_record_overrides(record_overrides)
    merged = dict(fetched_record)
    for key, value in fallback.items():
        if value is None:
            continue
        current = merged.get(key)
        is_empty = (
            current is None
            or (isinstance(current, str) and not current.strip())
            or (isinstance(current, list) and not current)
        )
        if is_empty:
            merged[key] = value
    return merge_record_sources(merged, normal)


def manual_record_from_overrides(record_overrides: Mapping[str, object]) -> NormalizedRecord:
    normal, fallback = split_record_overrides(record_overrides)
    return merge_record_sources(fallback, normal)


def pdf_result_fields(
    *,
    pdf_url: str | None,
    pdf_path: str | None,
    warnings: list[str],
    dry_run: bool,
) -> dict[str, str | None]:
    """Return structured PDF status fields for add/capture results."""
    if pdf_path is not None:
        return {
            "pdf_url": pdf_url,
            "pdf_status": "direct_saved",
            "pdf_error": None,
            "pdf_suggestion": None,
        }
    if pdf_url is None:
        return {
            "pdf_url": None,
            "pdf_status": "none",
            "pdf_error": None,
            "pdf_suggestion": None,
        }
    if dry_run:
        return {
            "pdf_url": pdf_url,
            "pdf_status": "found",
            "pdf_error": None,
            "pdf_suggestion": None,
        }

    error = warnings[0] if warnings else None
    return {
        "pdf_url": pdf_url,
        "pdf_status": "direct_blocked" if error else "found",
        "pdf_error": error,
        "pdf_suggestion": (
            "Use the browser extension for authenticated/browser-only PDFs, "
            "or configure browser_pdf_cmd."
            if error
            else None
        ),
    }


def _coerce_year(value: object) -> int | None:
    """Coerce a year value (str or int) to int.

    Returns ``None`` when the value cannot be coerced to a plausible year
    (1000–2099 inclusive).
    """
    if isinstance(value, int):
        return value if 1000 <= value <= 2099 else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except (ValueError, OverflowError):
            return None
        if 1000 <= parsed <= 2099:
            return parsed
    return None


def has_minimum_metadata(record: Mapping[str, object]) -> bool:
    """Return True when *record* has sufficient metadata for a fallback add.

    Requires a non-empty title plus at least one of: non-empty DOI,
    non-empty author list, or a plausible numeric year (int or string).
    """
    title = record.get("title")
    doi = record.get("doi")
    authors = record.get("authors")
    year = record.get("year")

    if not isinstance(title, str) or not title.strip():
        return False

    if isinstance(doi, str) and doi.strip():
        return True
    if isinstance(authors, list) and bool(authors):
        return True
    if _coerce_year(year) is not None:
        return True

    return False


def _answers_the_lookup(record: Mapping[str, object] | None) -> bool:
    """Whether a provider's answer is worth stopping the cascade for.

    Every normalizer returns a dict whether or not the response said anything —
    `_crossref_normalize_work` builds one with `title: None` for an empty
    `message` — and the cascade stopped at the first non-`None`. So a thin
    Crossref answer won permanently, and OpenAlex and Semantic Scholar were
    never consulted for it: the fallbacks existed for exactly this case and
    could not be reached. A record with a DOI but no title then passed the
    acceptance gate and was written.

    A title is the bar. It is what `has_minimum_metadata` requires, what the
    citekey is built from, and what distinguishes "the provider knows this
    paper" from "the provider answered".
    """
    if record is None:
        return False
    title = record.get("title")
    return isinstance(title, str) and bool(title.strip())


def identifies_a_paper(record: Mapping[str, object]) -> bool:
    """Whether *record* says which paper it is, by any means at all.

    The acceptance gate in front of every capture. A record with none of title,
    DOI, authors or year is not a capture: it used to be written as
    ``@article{unknownxxxxuntitled}`` with ``warnings: []`` and exit 0, so the
    library gained an entry naming no paper and the user was told it worked.

    Deliberately weaker than :func:`has_minimum_metadata`, which wants a title
    *and* one of DOI/authors/year. Whether a thin-but-identified record should be
    refused is decision 3, still open; a user who supplied authors by hand with
    ``--metadata-json`` has told us what this is. What needs no decision is a
    record that identifies nothing whatsoever.

    Shared by the URL/DOI branch and the local-PDF branch. It lived inline in
    the first of those, which is why the second wrote unidentifiable entries for
    a release after the gate was said to exist.
    """

    def _has_value(key: str) -> bool:
        value = record.get(key)
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return bool(value)
        return value is not None

    return any(_has_value(key) for key in ("title", "doi", "authors", "year"))


def minimum_metadata_diagnostics(record: Mapping[str, object]) -> list[str]:
    """Return human-readable lines explaining why metadata is insufficient."""
    lines: list[str] = []

    title = record.get("title")
    doi = record.get("doi")
    authors = record.get("authors")
    year = record.get("year")

    if not isinstance(title, str) or not title.strip():
        # Neutral: this is printed by `pzi add 10.x/y` on the command line just
        # as often as by a capture, and blaming the browser extension for a
        # run that never involved it sends the user to debug the wrong thing.
        lines.append("missing title: no source supplied one")
    else:
        contributors: list[str] = []
        if isinstance(doi, str) and doi.strip():
            contributors.append(f"doi={doi.strip()}")
        else:
            contributors.append("doi not available")
        if isinstance(authors, list) and bool(authors):
            contributors.append(f"{len(authors)} author(s)")
        else:
            contributors.append("authors not available")
        if _coerce_year(year) is not None:
            contributors.append(f"year={_coerce_year(year)}")
        else:
            contributors.append("year not available or not numeric")
        lines.append("title found but insufficient identifiers: " + "; ".join(contributors))

    return lines


def error_result(
    *,
    message: str,
    errors: list[str],
    dry_run: bool,
    warnings: list[str],
    bib: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "bib_name": bib["name"] if bib is not None else None,
        "bib_path": bib["path"] if bib is not None else None,
        "action": None,
        "citekey": None,
        "pdf_path": None,
        "changed_fields": [],
        "dry_run": dry_run,
        "message": message,
        "warnings": warnings,
        "errors": errors,
    }


def attach_similarity_hint(
    record: NormalizedRecord,
    existing_records: list[NormalizedRecord],
    *,
    exact_match_fn=find_exact_match,
    similarity_hint_fn=compute_similarity_hint,
    index: dict | None = None,
    force_new: bool = False,
) -> NormalizedRecord:
    # `find_exact_match` returns a *position*, not a record.
    exact_index = exact_match_fn(record, existing_records, index=index)
    if exact_index is not None:
        # An exact match is normally *handled* — the write becomes an update of
        # the matched entry — so hinting "possibly similar" about it would be
        # noise. Under `--force-new` it is not handled: a second entry for the
        # same paper is inserted deliberately, which is the one case that most
        # needs saying out loud. Returning early for both is why
        # `import --force-new` doubled a library with `warnings: []`.
        if not force_new:
            return record
        try:
            matched = existing_records[exact_index]
        except (IndexError, TypeError):  # pragma: no cover - defensive
            return record
        matched_citekey = matched.get("citekey")
        if not isinstance(matched_citekey, str) or not matched_citekey.strip():
            return record
        duplicated = dict(record)
        duplicated["duplicate_of"] = matched_citekey
        return cast(NormalizedRecord, duplicated)

    incoming_citekey = record.get("citekey")
    candidates = [
        existing
        for existing in existing_records
        if existing.get("citekey") != incoming_citekey
    ]
    hint_citekey = similarity_hint_fn(record, candidates)
    if hint_citekey is None:
        return record

    hint_text = f"Possibly similar to {hint_citekey}"
    existing_note = record.get("note")
    updated = dict(record)
    # Record the match structurally as well as in the note. The note is what
    # survives into the .bib; ``similarity_hint`` is what lets the caller warn
    # the user without re-parsing prose out of the note field.
    updated["similarity_hint"] = hint_citekey
    if isinstance(existing_note, str) and existing_note.strip():
        # Re-capturing an already-hinted entry must not append the hint twice,
        # but it still deserves the warning — so set the field either way.
        if hint_text not in existing_note:
            updated["note"] = f"{existing_note.strip()}; {hint_text}"
    else:
        updated["note"] = hint_text
    return cast(NormalizedRecord, updated)


def similarity_hint_warnings(record: Mapping[str, object]) -> list[str]:
    """User-facing warnings for a fuzzy near-duplicate, if one was hinted.

    :func:`attach_similarity_hint` only writes the match into the entry's
    ``note`` field, so a near-duplicate was discoverable solely by reading the
    ``.bib`` afterwards or running ``pzi fix dedupe`` — a capture that quietly
    doubled an entry looked identical to a clean one.

    An exact identity match under ``--force-new`` gets its own, stronger line:
    it is not a maybe, and the user has just been handed a second entry for a
    paper the library already had.
    """
    duplicate = record.get("duplicate_of")
    if isinstance(duplicate, str) and duplicate.strip():
        return [
            f"inserted a second entry for a paper already in the library as "
            f"{duplicate} (--force-new); merge them with `pzi fix merge`"
        ]
    hint = record.get("similarity_hint")
    if not isinstance(hint, str) or not hint.strip():
        return []
    return [f"possibly a duplicate of {hint} — compare them with `pzi fix dedupe`"]


# ---------------------------------------------------------------------------
# Metadata fetching pipeline (merged from _record_fetching.py)
# ---------------------------------------------------------------------------


def build_discovery_context(
    *,
    raw_value: str,
    server_url: str,
    unpaywall_email: str | None = None,
    contact_email: str | None = None,
    s2_api_key: str | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    pdf_url_candidates: list[str] | None = None,
    cookies: str | None = None,
    fetch_web: WebTranslationFetcher | None = None,
    fetch_unpaywall: UnpaywallFetcher | None = None,
    fetch_crossref: MetadataRecordFetcher | None = None,
    fetch_openalex: MetadataRecordFetcher | None = None,
    fetch_s2: S2RecordFetcher | None = None,
    fetch_flaresolverr: HtmlFetcher | None = None,
    translation_attachments: list[dict[str, object]] | None = None,
    api_url: str | None = None,
    api_auth_token: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    pdf_discovery_parallel: bool = False,
    exclude_pdf_urls: frozenset[str] | None = None,
) -> PdfDiscoveryContext:
    """Assemble the context dict consumed by the PDF-discovery steps.

    Single source of truth for the context shape: both the normal fetch path
    (:func:`fetch_record_for_input`) and the translation-server-failure fallback
    in :mod:`pzi.add_service` build their context here, so the two can no longer
    drift (the fallback previously hand-rolled a subset and silently omitted
    keys like ``cookies`` / ``contact_email``).

    ``exclude_pdf_urls`` carries the URLs a download has already failed on, so a
    re-run yields the next source instead of the same dead one.
    """
    return {
        "raw_value": raw_value,
        "server_url": server_url,
        "unpaywall_email": unpaywall_email,
        "contact_email": contact_email,
        "s2_api_key": s2_api_key,
        "flaresolverr_url": flaresolverr_url,
        "browser_pdf_cmd": browser_pdf_cmd,
        "pdf_url_candidates": pdf_url_candidates,
        "cookies": cookies,
        "fetch_web": fetch_web,
        "fetch_unpaywall": fetch_unpaywall,
        "fetch_crossref": fetch_crossref,
        "fetch_openalex": fetch_openalex,
        "fetch_s2": fetch_s2,
        "fetch_flaresolverr": fetch_flaresolverr,
        "translation_attachments": translation_attachments,
        "api_url": api_url,
        "api_auth_token": api_auth_token,
        "desktop_fallback_hosts": desktop_fallback_hosts,
        "pdf_discovery_parallel": pdf_discovery_parallel,
        "exclude_pdf_urls": exclude_pdf_urls,
    }


def next_pdf_candidate_for_config(config: AppConfig, bib: BibConfig) -> NextPdfCandidate:
    """Re-run PDF discovery for a *stored* entry, excluding what has failed.

    `pdf retry` and `promote` work from a bib entry rather than a live capture,
    so they hold no discovery context and used to run no discovery at all: they
    took the one stored `pdf_url`, pushed it through every *transport* fallback,
    and gave up — leaving the entry in exactly the state that made the user run
    the command.

    Credentials come from `build_capture_context` rather than being re-read from
    config here, so their resolution keeps one implementation. Note that
    `pdf_service._fallback_kwargs` skips that builder on the grounds that these
    credentials "play no part in fetching a PDF"; that stopped being true when
    the open-access fallback became part of fetching a PDF.

    The add path builds its own closure instead, because it *has* a live context
    carrying the caller's injected fetchers and cookies — which is strictly more
    than can be reconstructed from config.

    The capture context is built on first use, not here. Building it resolves
    credentials, and `unpaywall_email_cmd` / `semantic_scholar_api_key_cmd`
    resolve by *running a shell command* — so doing it eagerly would charge
    every `pdf retry` for secrets it needs only when a download has already
    failed, which is the eager cost this whole fallback is designed to avoid.
    """
    cached: list[CaptureContext] = []

    def _context() -> CaptureContext:
        if not cached:
            cached.append(
                build_capture_context(
                    config=config, bib=bib, browser_pdf_cmd_override=None, browser=None
                )
            )
        return cached[0]

    def _next(record: NormalizedRecord, tried: frozenset[str]) -> NormalizedRecord | None:
        context = _context()
        return apply_pdf_discovery(
            record,
            DEFAULT_DISCOVERY_STEPS,
            build_discovery_context(
                raw_value=str(
                    record.get("source_url")
                    or record.get("canonical_url")
                    or record.get("doi")
                    or ""
                ),
                server_url=config["translation_server_url"],
                unpaywall_email=context.unpaywall_email,
                contact_email=context.contact_email,
                s2_api_key=context.s2_api_key,
                flaresolverr_url=config.get("flaresolverr_url"),
                browser_pdf_cmd=context.browser_pdf_cmd,
                api_url=context.api_url,
                api_auth_token=context.api_auth_token,
                desktop_fallback_hosts=context.desktop_fallback_hosts,
                pdf_discovery_parallel=context.pdf_discovery_parallel,
                exclude_pdf_urls=tried,
            ),
        )

    return _next


def _carry_item_type(record: dict[str, Any], selected: Mapping[str, Any]) -> None:
    """Copy a translation result's ``item_type`` into the record it wraps.

    ``normalize_translation_item`` returns ``item_type`` as a *sibling* of
    ``record``, and the add path took only the record — so ``resolve_entry_type``
    saw no item type and fell through to its ``"article"`` default. Every
    conference paper captured through the translation-server became
    ``@article`` with ``journal = {proceedings title}``, and since Crossref,
    OpenAlex and DBLP *do* put ``item_type`` inside their records, the entry type
    silently depended on which provider answered. ``promote_service`` has always
    carried it correctly; this is the same move.
    """
    item_type = selected.get("item_type")
    if not isinstance(item_type, str) or not item_type.strip():
        return
    if item_type.strip() == "webpage":
        # Zotero's "I could not tell" answer, not a claim about the work. Taking
        # it literally would retype every unrecognized publisher page as
        # `@unpublished`; the existing heuristics (preprint source, declared
        # type, else article) make a better guess from the metadata itself.
        return
    record.setdefault("item_type", item_type.strip())


class MetadataExhausted(ValueError):
    """Every metadata source was tried and none produced a record.

    Carries the errors accumulated on the way. They used to be discarded: this
    function returns `provider_errors` only on the *success* path, so a total
    failure — the case where knowing which providers failed and how matters most
    — surfaced as one sentence with no evidence. `--strict-metadata` then had
    nothing to be strict about, and a caller could not tell "this DOI does not
    exist" from "all five providers were rate-limited".

    Subclasses `ValueError` so the existing handlers keep catching it.
    """

    def __init__(self, message: str, provider_errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.provider_errors = list(provider_errors or [])


def fetch_record_for_input(
    *,
    raw_value: str,
    classified: Mapping[str, object],
    server_url: str,
    fetch_web: WebTranslationFetcher,
    fetch_search: SearchTranslationFetcher,
    contact_email: str | None = None,
    unpaywall_email: str | None = None,
    s2_api_key: str | None = None,
    flaresolverr_url: str | None = None,
    fetch_unpaywall: UnpaywallFetcher | None = None,
    fetch_crossref: MetadataRecordFetcher | None = None,
    fetch_openalex: MetadataRecordFetcher | None = None,
    fetch_s2: S2RecordFetcher | None = None,
    fetch_flaresolverr: HtmlFetcher | None = None,
    pdf_url_candidates: list[str] | None = None,
    browser_pdf_cmd: str | None = None,
    cookies: str | None = None,
    api_url: str | None = None,
    api_auth_token: str | None = None,
    desktop_fallback_hosts: set[str] | None = None,
    pdf_discovery_parallel: bool = False,
    metadata_fetch_text: Callable[..., str] | None = None,
) -> tuple[NormalizedRecord, list[str], list[dict]]:
    provider_errors: list[str] = []
    # The raw translation-server results, returned so the caller can compute
    # diagnostics where it consumes them. They used to be reported by having the
    # caller wrap the injected fetchers and assign to `nonlocal` variables, so a
    # retry that re-invoked a fetcher silently overwrote the diagnostics.
    translation_results: list[dict] = []
    kind = classified["kind"]
    normalized = cast(str | None, classified["normalized"])
    fallback = _fallback_record_for_input(
        kind=cast(str, kind), normalized=normalized, raw_value=raw_value
    )

    def _discovery_context(
        translation_attachments: list[dict[str, object]] | None = None,
    ) -> PdfDiscoveryContext:
        return build_discovery_context(
            raw_value=raw_value,
            server_url=server_url,
            unpaywall_email=unpaywall_email,
            contact_email=contact_email,
            s2_api_key=s2_api_key,
            flaresolverr_url=flaresolverr_url,
            browser_pdf_cmd=browser_pdf_cmd,
            pdf_url_candidates=pdf_url_candidates,
            cookies=cookies,
            fetch_web=fetch_web,
            fetch_unpaywall=fetch_unpaywall,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_s2=fetch_s2,
            fetch_flaresolverr=fetch_flaresolverr,
            translation_attachments=translation_attachments,
            api_url=api_url,
            api_auth_token=api_auth_token,
            desktop_fallback_hosts=desktop_fallback_hosts,
            pdf_discovery_parallel=pdf_discovery_parallel,
        )

    def _with_pdf_discovery(
        base_record: NormalizedRecord,
        *,
        translation_attachments: list[dict[str, object]] | None = None,
    ) -> NormalizedRecord:
        # Strip bare DOI redirect URLs — they are not downloadable PDFs.
        # Let discovery steps find the actual PDF URL.
        pdf_url = base_record.get("pdf_url")
        if isinstance(pdf_url, str) and pdf_url.startswith("https://doi.org/"):
            base_record = cast(NormalizedRecord, dict(base_record))
            base_record.pop("pdf_url", None)

        context = _discovery_context(translation_attachments=translation_attachments)
        if pdf_discovery_parallel:
            from pzi.pdf_discovery import apply_pdf_discovery_parallel as _parallel
            found = _parallel(base_record, DEFAULT_DISCOVERY_STEPS, context)
        else:
            found = apply_pdf_discovery(base_record, DEFAULT_DISCOVERY_STEPS, context)
        # A step that raised is treated as "no result" so the fan-out continues,
        # which is right — but with nothing found and nothing said, a
        # permanently broken provider looks exactly like a paper with no
        # open-access copy. Carried on the record so `--verbose` and `--json`
        # can show it; only when there is no PDF, since a successful discovery
        # does not owe the user a report on the sources it did not need.
        if not found.get("pdf_url"):
            failures = discovery_diagnostics(context)
            if failures:
                enriched = dict(found)
                enriched["pdf_discovery_diagnostics"] = failures
                found = cast(NormalizedRecord, enriched)
        return found

    if kind == "doi" and normalized is not None:
        results = safe_api_call(
            lambda: fetch_search(normalized, server_url=server_url),
            errors=provider_errors,
        )
        # A candidate claiming a different DOI is a different paper, whatever it
        # scores. Dropping it here rather than at selection lets the cascade fall
        # through to Crossref/OpenAlex, which is what a caller asking for a
        # specific DOI wants next.
        usable = drop_contradicting_candidates(results or [], fallback)
        # The *usable* hits, not the raw ones. `--verbose` reads this list to
        # name the candidate a capture came from, so recording everything the
        # provider returned meant the diagnostic named a candidate dropped for
        # contradicting the requested DOI, and warned "metadata confidence low"
        # about a capture that was correct.
        translation_results.extend(cast("list[dict]", usable))
        if usable:
            selected = select_best_metadata_result(usable, fallback)
            best = dict(merge_record_sources(fallback, selected["record"]))
            _carry_item_type(best, selected)
            return (
                _with_pdf_discovery(
                    cast(NormalizedRecord, best),
                    translation_attachments=selected.get("attachments"),
                ),
                provider_errors,
                translation_results,
            )

        # The cascade, in priority order, with the winner recorded. Nothing
        # anywhere used to say which provider answered, so `--verbose` on the
        # commonest invocation of all — a DOI that Crossref resolves on the
        # first try — printed nothing at all.
        meta = None
        winner: str | None = None
        for provider, call in (
            ("crossref", lambda: _call_metadata_fetcher(
                fetch_crossref or fetch_crossref_record,
                normalized,
                contact_email=contact_email,
                errors=provider_errors,
                fetch_text=metadata_fetch_text,
            )),
            ("openalex", lambda: _call_metadata_fetcher(
                fetch_openalex or fetch_openalex_record,
                normalized,
                contact_email=contact_email,
                errors=provider_errors,
                fetch_text=metadata_fetch_text,
            )),
            # `fetch_s2` is `S2RecordFetcher`, a one-argument callable, so it
            # cannot go through `_call_metadata_fetcher` the way the other two
            # do without breaking its own declared contract. It gets the same
            # *error handling* instead: called bare, an injected fetcher raising
            # `HTTPError` aborted the whole cascade, and its failures never
            # reached `provider_errors`, so `--strict-metadata` could not see
            # them either.
            ("semantic_scholar", lambda: _fetch_s2_guarded(
                fetch_s2,
                normalized,
                s2_api_key=s2_api_key,
                errors=provider_errors,
                fetch_text=metadata_fetch_text,
            )),
        ):
            candidate = call()
            if _answers_the_lookup(candidate):
                meta = candidate
                winner = provider
                break
            if candidate is not None and meta is None:
                # Keep the first thin answer as a floor, so a DOI that only
                # Crossref knows about still yields what little it said when no
                # later provider does better.
                meta = candidate
        if meta is not None:
            best = dict(merge_record_sources(fallback, meta))
            record = _with_pdf_discovery(cast(NormalizedRecord, best))
            if winner:
                enriched = dict(record)
                enriched["metadata_provider"] = winner
                record = cast(NormalizedRecord, enriched)
            return record, provider_errors, translation_results

        raw_as_url = (
            raw_value if urlsplit(raw_value).scheme in {"http", "https"} else None
        )
        if raw_as_url:
            web_results = safe_api_call(
                lambda: fetch_web(raw_as_url, server_url=server_url)
                if cookies is None
                else fetch_web(raw_as_url, server_url=server_url, cookies=cookies),
                errors=provider_errors,
            )
            usable_web = drop_contradicting_candidates(web_results or [], fallback)
            translation_results.extend(cast("list[dict]", usable_web))
            if usable_web:
                # Scored and item-type-carrying, like the DOI-search branch above
                # and the URL branch below. Taking `[0]` meant this one fallback
                # path picked whichever result the translator happened to emit
                # first and typed every conference paper it found as `@article`.
                selected = select_best_metadata_result(usable_web, fallback)
                best = dict(merge_record_sources(fallback, selected["record"]))
                _carry_item_type(best, selected)
                return (
                    _with_pdf_discovery(
                        cast(NormalizedRecord, best),
                        translation_attachments=selected.get("attachments"),
                    ),
                    provider_errors,
                    translation_results,
                )

            if flaresolverr_url is not None:  # pragma: no branch
                fn = fetch_flaresolverr or (
                    lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
                )
                html = fn(raw_as_url)
                if html:  # pragma: no branch — covered by integration/browser tests
                    meta = extract_metadata_from_html(html)
                    if meta is not None:  # pragma: no branch — covered by integration/browser tests
                        best = dict(merge_record_sources(meta, fallback))
                        return (
                _with_pdf_discovery(cast(NormalizedRecord, best)),
                provider_errors,
                translation_results,
            )

        suffix = (
            " (page may be Cloudflare-protected — configure flaresolverr_url to bypass)"
            if raw_as_url and flaresolverr_url is None
            else ""
        )
        raise MetadataExhausted(
            f"no metadata found for DOI: {normalized}{suffix}", provider_errors
        )

    if kind in {"url", "pdf_url"} and normalized is not None:
        results = safe_api_call(
            lambda: fetch_web(normalized, server_url=server_url)
            if cookies is None
            else fetch_web(normalized, server_url=server_url, cookies=cookies),
            errors=provider_errors,
        )
        translation_results.extend(results or [])
        if results:
            selected = select_best_metadata_result(results, fallback)
            best = dict(selected["record"])
            _carry_item_type(best, selected)
            best = _with_pdf_discovery(
                cast(NormalizedRecord, best), translation_attachments=selected.get("attachments")
            )
            return merge_record_sources(fallback, best), provider_errors, translation_results

        if flaresolverr_url is not None:
            fn = fetch_flaresolverr or (
                lambda u: fetch_html_via_flaresolverr(u, server_url=flaresolverr_url)
            )
            html = fn(normalized)
            if html:
                meta = extract_metadata_from_html(html)
                if meta is not None:
                    best = dict(merge_record_sources(meta, fallback))
                    return (
                _with_pdf_discovery(cast(NormalizedRecord, best)),
                provider_errors,
                translation_results,
            )

        raise MetadataExhausted(
            f"translation server returned no results for URL: {normalized}",
            provider_errors,
        )

    # Unreachable in normal flow: `unknown` input is rejected upstream by
    # describe_invalid_add_input before reaching here.  Guard defensively so a
    # future caller cannot silently insert an empty placeholder record.
    raise ValueError(  # pragma: no cover - defensive; unknown is rejected upstream
        f"unrecognized input (not a DOI, URL, or PDF): {raw_value!r}"
    )


def _describe_http_failure(exc: urllib.error.HTTPError) -> str:
    """A provider's HTTP status, in the terms the user can act on.

    Every status was reported as bare ``HTTP <code>``. That is fine for a 500
    and misleading for 300: the translation server answers `/web` with **300
    Multiple Choices** when a page yields several candidate items, returning a
    selection map rather than an item list (`src/webEndpoint.js` at the pinned
    commit). pzi does not choose among them, so the capture correctly finds
    nothing — but "HTTP 300" reads as a broken server rather than as a page that
    needs a more specific URL.
    """
    if exc.code == 300:
        return (
            "the page offered several possible items and pzi does not choose "
            "between them — capture the specific article URL instead"
        )
    return f"HTTP {exc.code}"


def safe_api_call(fn, *, errors: list[str] | None = None):
    """Run callable, returning [] on an expected provider failure.

    Two expected failure modes are absorbed so the cascade can fall through to
    the next provider: an HTTP status, and a transport error — the
    translation-server being down is the common one, and catching only
    ``HTTPError`` meant it aborted DOI resolution entirely while Crossref and
    OpenAlex sat right behind it. The reason is recorded either way, so
    ``--strict-metadata`` and the result's warnings still see it.

    A ``ValueError``/``KeyError``/``TypeError`` deliberately propagates: that is
    a bug in pzi or a provider contract change, and turning it into "no results"
    is how a broken provider comes to look like an unindexed paper. See
    ``test_add_input_to_bib_does_not_swallow_non_network_bugs``.
    """
    try:
        return fn()
    except urllib.error.HTTPError as exc:
        if errors is not None:
            errors.append(_describe_http_failure(exc))
        return []
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if errors is not None:
            errors.append(f"provider unreachable: {exc}")
        return []



def drop_contradicting_candidates(
    results: Sequence[Mapping[str, Any]], fallback: Mapping[str, object]
) -> list[Mapping[str, Any]]:
    """Results that do not claim a *different* DOI than the one asked for.

    Scoring only penalized this, by 50 points, which a rich record outweighs —
    and when the contradicting candidate is the only one it wins outright
    however low it scores. `pzi add 10.1145/A` then stored the record for
    `10.9999/B`: a different paper, under the citekey and DOI the user asked
    for, indistinguishable afterwards from a correct capture.

    A candidate that agrees, or says nothing about the DOI, is kept: most
    translators return no DOI at all, so refusing those would refuse most
    captures.
    """
    wanted = _norm_text(fallback.get("doi"))
    if not wanted:
        return list(results)
    kept: list[Mapping[str, Any]] = []
    for result in results:
        record = result.get("record")
        found = _norm_text(record.get("doi")) if isinstance(record, Mapping) else None
        if found is None or found == wanted:
            kept.append(result)
    return kept


def select_best_metadata_result(
    results: Sequence[Mapping[str, Any]], fallback: Mapping[str, object]
) -> Mapping[str, Any]:
    """Choose best metadata result by pure score, preserving input order on ties."""
    if not results:
        raise ValueError("metadata results cannot be empty")
    return max(
        enumerate(results),
        key=lambda item: (score_metadata_candidate(item[1], fallback), -item[0]),
    )[1]


def metadata_result_diagnostics(
    results: list[Mapping[str, Any]], fallback: Mapping[str, object]
) -> list[str]:
    """Pure human-readable diagnostics for metadata result scoring."""
    if not results:
        return []
    scored = [
        (index, result, score_metadata_candidate(result, fallback))
        for index, result in enumerate(results)
    ]
    best_index, best_result, best_score = max(
        scored,
        key=lambda item: (item[2], -item[0]),
    )
    lines = [
        _metadata_diagnostic_line(
            "selected", best_index, len(results), best_score, best_result
        )
    ]
    lines.extend(
        _metadata_diagnostic_line("rejected", index, len(results), score, result)
        for index, result, score in scored
        if index != best_index
    )
    return lines


def metadata_result_confidence_warnings(
    results: list[Mapping[str, Any]],
    fallback: Mapping[str, object],
    *,
    min_score: int = 0,
) -> list[str]:
    """Pure warnings for low-confidence selected metadata results."""
    if not results:
        return []
    selected = select_best_metadata_result(results, fallback)
    score = score_metadata_candidate(selected, fallback)
    if score >= min_score:
        return []
    return [
        "metadata confidence low: "
        f"selected result score={score} below {min_score}; verify captured metadata"
    ]


def score_metadata_candidate(
    result: Mapping[str, Any], fallback: Mapping[str, object]
) -> int:
    """Pure quality score for translation-server metadata candidates."""
    record = result.get("record")
    if not isinstance(record, Mapping):
        return -1000
    score = 0
    score += _identifier_score(record, fallback)
    score += _metadata_richness_score(record)
    score += _attachment_score(result)
    return score


def _identifier_score(record: Mapping[str, object], fallback: Mapping[str, object]) -> int:
    score = 0
    fallback_doi = _norm_text(fallback.get("doi"))
    record_doi = _norm_text(record.get("doi"))
    if fallback_doi and record_doi:
        score += 50 if fallback_doi == record_doi else -50
    fallback_arxiv = _norm_text(fallback.get("arxiv_id"))
    record_arxiv = _norm_text(record.get("arxiv_id"))
    if fallback_arxiv and record_arxiv:
        score += 40 if fallback_arxiv == record_arxiv else -20
    return score


def _metadata_richness_score(record: Mapping[str, object]) -> int:
    score = 0
    for key in ("title", "venue", "doi", "arxiv_id", "abstract_url", "canonical_url"):
        if _norm_text(record.get(key)):
            score += 3
    if isinstance(record.get("year"), int):
        score += 3
    authors = record.get("authors")
    if isinstance(authors, list) and authors:
        score += min(len(authors), 3) * 2
    return score


def _attachment_score(result: Mapping[str, Any]) -> int:
    attachments = result.get("attachments")
    if isinstance(attachments, list) and attachments:
        return 2
    return 0


def _norm_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def _metadata_diagnostic_line(
    status: str,
    index: int,
    total: int,
    score: int,
    result: Mapping[str, Any],
) -> str:
    record = result.get("record")
    if not isinstance(record, Mapping):
        return f"{status} result {index + 1}/{total}: score={score}; invalid record"
    parts = [f"{status} result {index + 1}/{total}: score={score}"]
    doi = record.get("doi")
    title = record.get("title")
    venue = record.get("venue")
    year = record.get("year")
    if isinstance(doi, str) and doi.strip():
        parts.append(f"doi={doi.strip()}")
    if isinstance(title, str) and title.strip():
        parts.append(f"title={title.strip()}")
    if isinstance(venue, str) and venue.strip():
        parts.append(f"venue={venue.strip()}")
    if isinstance(year, int):
        parts.append(f"year={year}")
    return "; ".join(parts)


def _fetch_s2_guarded(
    fetch_s2,
    doi: str,
    *,
    s2_api_key: str | None,
    errors: list[str],
    fetch_text,
):
    """Call the Semantic Scholar seam with the cascade's error contract.

    The default fetcher already reports into *errors* and returns None. An
    injected one is a plain one-argument callable that may raise, and it was
    called bare — so a rate-limited or unreachable S2 aborted the whole DOI
    cascade before the URL and FlareSolverr branches below ever ran.
    """
    if fetch_s2 is None:
        return fetch_semantic_scholar_record(
            doi, api_key=s2_api_key, errors=errors, fetch_text=fetch_text
        )
    try:
        return fetch_s2(doi)
    except urllib.error.HTTPError as exc:
        errors.append(f"HTTP {exc.code}")
        return None
    except (OSError, TimeoutError) as exc:
        errors.append(str(exc))
        return None


def _call_metadata_fetcher(
    fn,
    doi: str,
    *,
    contact_email: str | None,
    errors: list[str] | None = None,
    fetch_text: Callable[..., str] | None = None,
):
    # Every fetcher conforms to MetadataRecordFetcher, so call it one way. The
    # old shape probed with `except TypeError` and retried with fewer arguments,
    # which silently converted a genuine TypeError *inside* a fetcher into a
    # plausible-looking fallback result.
    kwargs: dict[str, Any] = {"contact_email": contact_email, "errors": errors}
    # `fetch_text` is deliberately not part of MetadataRecordFetcher: the real
    # fetchers accept it and injected ones need not. Passing it when it fits is
    # what routes this path through the configured cache and rate limiter.
    if fetch_text is not None and accepts_keyword(fn, "fetch_text"):
        kwargs["fetch_text"] = fetch_text
    try:
        return fn(doi, **kwargs)
    except urllib.error.HTTPError as exc:
        if errors is not None:
            errors.append(f"HTTP {exc.code}")
        return None
    except (OSError, TimeoutError) as exc:
        if errors is not None:
            errors.append(str(exc))
        return None


def _fallback_record_for_input(
    *, kind: str, normalized: str | None, raw_value: str
) -> NormalizedRecord:
    if kind == "doi" and normalized is not None:
        record: NormalizedRecord = {"doi": normalized}
        raw_as_url = raw_value if _url_is_http(raw_value) else None
        if raw_as_url is not None:
            record["canonical_url"] = raw_as_url
            record["source_url"] = raw_as_url
            record["abstract_url"] = raw_as_url
        arxiv_id = _arxiv_id_from_doi(normalized)
        if arxiv_id is not None:
            record["arxiv_id"] = arxiv_id
        return record
    if kind == "pdf_url" and normalized is not None:
        return {"pdf_url": normalized, "source_url": normalized}
    if kind == "url" and normalized is not None:
        return {
            "canonical_url": normalized,
            "source_url": normalized,
            "abstract_url": normalized,
        }
    return {"source_url": raw_value}


def _url_is_http(value: str) -> bool:
    try:
        return urlsplit(value).scheme in {"http", "https"}
    except ValueError:
        return False


def _arxiv_id_from_doi(doi: str) -> str | None:
    """Extract bare arXiv ID from an arXiv DOI like 10.48550/arXiv.1106.5249."""
    lower = doi.lower()
    prefix = "10.48550/arxiv."
    if lower.startswith(prefix):
        return doi[len(prefix):]
    return None


def merge_record_sources(
    base: Mapping[str, object], overrides: Mapping[str, object]
) -> NormalizedRecord:
    merged = dict(base)
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value
    # NormalizedRecord declares year: int | None, but fallback sources (e.g.
    # browser-scraped page metadata) supply it as a string. Coerce here, the
    # single choke point where a NormalizedRecord is produced, so every caller
    # gets a real int rather than crashing downstream (e.g. similarity's
    # abs(record_year - existing_year)).
    if "year" in merged:
        merged["year"] = _coerce_year(merged["year"])
    return cast(NormalizedRecord, merged)

