"""Preprint promotion service: find published versions and update or fork entries."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from pzi import promote_ledger
from pzi.add_planning import next_pdf_candidate_for_config
from pzi.bib_repository import (
    BatchWriteSession,
    ConcurrentEditError,
    StalePlanError,
    WritePlan,
    backup_path_for,
    batch_write_session,
    plan_bib_write,
    preview_batch_write,
    preview_write_plan,
    read_bib_file,
    resolve_entry_type,
)
from pzi.bibtex import (
    USER_OWNED_FIELDS,
    BibtexEntry,
    NormalizedRecord,
    bibtex_entry_to_record,
    generate_citekey,
    merge_projected_entry,
    normalize_authors,
    record_to_bibtex_entry,
    venue_field_for_entry_type,
)
from pzi.bibtex import changed_fields as changed_fields_between
from pzi.capture_context import resolve_contact_email, resolve_optional_value
from pzi.config import BibResolutionFailure, load_bib_target
from pzi.errors import REASON_CONFIG, REASON_UNAVAILABLE
from pzi.fetch_helpers import ProviderBreaker, build_metadata_fetch_text
from pzi.format_templates import format_citekey
from pzi.identifiers import (
    has_preprint_identity,
    is_preprint_doi,
    is_preprint_url,
    names_a_preprint_server,
)
from pzi.pdf import NextPdfCandidate, fetch_and_store_pdf_trying_sources
from pzi.pdf import remove_new_pdf as _remove_new_pdf
from pzi.pdf import snapshot_pdf_paths as _snapshot_pdf_paths
from pzi.promote_planning import (
    AcceptanceGate,
    find_published_candidate_with_diagnostics,
    published_candidate_confidence_warnings,
)
from pzi.protocols import (
    BinaryFetcher,
    MetadataRecordFetcher,
    S2RecordWithErrorFetcher,
    SearchTranslationFetcher,
)
from pzi.resolution_match import score_match
from pzi.similarity import canonical_doi, normalize_title
from pzi.tag_service import add_tags


class PromoteItem(TypedDict):
    """One preprint considered for promotion, inside a `PromoteResult`."""

    preprint_citekey: str
    published_citekey: str | None
    action: str
    changed_fields: list[str]
    pdf_attached: bool | None
    note: str | None
    #: Set when this preprint could not be promoted because something *went
    #: wrong* (as opposed to nothing to do). The runner reports PARTIAL from
    #: this; without it `exit_codes.PARTIAL` was unreachable and a run where
    #: every promotion failed exited 0 with `errors: []`.
    failed: NotRequired[bool]
    diff: NotRequired[str]
    #: Where the pre-promotion library was copied, on the `--replace` path. A
    #: backup nobody can find is not an undo.
    backup_path: NotRequired[str]
    metadata_diagnostics: NotRequired[list[str]]
    metadata_warnings: NotRequired[list[str]]


class PromoteResult(TypedDict):
    """A promotion sweep — what `pzi.promote()` returns.

    One `PromoteItem` per preprint the sweep considered, whether or not it was
    promoted. Previews by default; `dry_run` says which this was.
    """

    status: str
    bib_name: str | None
    dry_run: bool
    keep_preprint: bool
    items: list[PromoteItem]
    errors: list[str]
    summary: NotRequired[dict[str, Any]]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
#: A promotion must clear this on *title* similarity alone, on top of the
#: composite confidence gate. The composite is not enough: a published version
#: is the same paper, so the one thing that survives the preprint→published
#: transition is the title, while identical authors are exactly what makes the
#: dangerous case dangerous. Measured on 20 real preprints, every one of the 12
#: correct promotions scored `title 100`; the two wrong ones scored `title 62`
#: and `title 70` with `author 100`, and the composite was dragged over the
#: threshold of 60 by that perfect author match. Both were a *different paper by
#: the same authors* — "On Adaptivity and Confounding in **Contextual** Bandit
#: Experiments" for a multi-armed-bandit preprint, and a conference *demo*
#: paper for the system it demonstrates. 85 is `resolution_match`'s own mark for
#: a title strong enough to anchor a decision, and it sits clear of both.
_MIN_TITLE_SIMILARITY = 85

# Tag written to a preprint by `--mark-resolved` so re-runs can skip it.
_RESOLVED_TAG = "promoted"
#: Skip reasons that are also summary keys. A record with no citekey, or
#: with no preprint identity, is not a *skip* the user asked about — it was
#: never a candidate — so those two are deliberately not counted.
_COUNTED_SKIPS = frozenset({"skipped_already_resolved", "skipped_recently_checked"})
#: Promotions written per session. Each session takes the lock, re-reads and
#: re-serialises the whole library — about 2.5 s against the 15.8 MB library
#: configured here — so writing one at a time made a sweep's writes cost more
#: than its lookups. Batching them all instead would mean an interrupted
#: sweep, and these run for hours, threw away every promotion it had found.
#: At 25 the write overhead is ~0.1 s per promotion and an interrupt costs at
#: most 24.
_WRITE_CHUNK = 25

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def promote_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    keep_preprint: bool = False,
    dry_run: bool = True,
    fetch_search: SearchTranslationFetcher | None = None,
    fetch_crossref: MetadataRecordFetcher | None = None,
    fetch_openalex: MetadataRecordFetcher | None = None,
    fetch_dblp: MetadataRecordFetcher | None = None,
    fetch_openreview: MetadataRecordFetcher | None = None,
    fetch_s2: S2RecordWithErrorFetcher | None = None,
    fetch_binary: BinaryFetcher | None = None,
    flaresolverr_url: str | None = None,
    browser_pdf_cmd: str | None = None,
    mark_resolved: bool = False,
    limit: int | None = None,
    best_of: int = 1,
    on_item: Callable[[PromoteItem, int, int], None] | None = None,
) -> PromoteResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "reason": REASON_CONFIG,
            "bib_name": None,
            "dry_run": dry_run,
            "keep_preprint": keep_preprint,
            "items": [],
            "errors": resolved.errors,
        }
    config, bib = resolved
    s2_api_key = resolve_optional_value(
        command=config.get("semantic_scholar_api_key_cmd"),
        fallback=config.get("semantic_scholar_api_key"),
    )
    contact_email = resolve_contact_email(config)
    effective_flaresolverr_url = flaresolverr_url or config.get("flaresolverr_url")
    effective_browser_pdf_cmd = browser_pdf_cmd or config.get("browser_pdf_cmd")
    file_path_style = str(config.get("pdf_file_path_style", "absolute"))
    # Subscript, not `.get(..., default)`: `AppConfig` is a total TypedDict, so
    # the loader always supplies this. The old fallback of 3 predated the move
    # to `score_match`'s 0-100 scale (where the default is 60) and would have
    # meant an effectively open gate had it ever been reachable.
    effective_confidence_threshold = config["promote_confidence_threshold"]
    # The same gate applied below, handed to discovery so it knows when it may
    # stop asking providers — one rule, one place it is defined.
    gate = AcceptanceGate(
        min_score=effective_confidence_threshold,
        min_title_similarity=_MIN_TITLE_SIMILARITY,
    )
    # Compose the metadata fetcher once (opt-in disk cache + per-host rate
    # limiting); the resolver uses it as the default for its title-search
    # providers unless a fetcher override is injected (e.g. by tests).
    metadata_fetch_text = build_metadata_fetch_text(config, api_key=s2_api_key)

    # The negative-lookup ledger. Loaded before the read so a disabled horizon
    # costs nothing at all: nothing is consulted and nothing is written.
    recheck_after_days = config["promote_recheck_after_days"]
    ledger_file = promote_ledger.ledger_path(config["pzi_data_home"])
    ledger = promote_ledger.load(ledger_file) if recheck_after_days > 0 else {}
    ledger_now = promote_ledger.utc_now()
    #: Citekeys this run confirmed are still unpublished, written once at the
    #: end rather than per item.
    checked_negative: list[str] = []

    read_result = read_bib_file(bib["path"])
    records = read_result["records"]
    known_records = list(records)
    existing_citekeys = {
        ck for r in records for ck in [r.get("citekey")] if isinstance(ck, str)
    }

    items: list[PromoteItem] = []
    summary = _empty_summary()
    resolved_preprints: list[str] = []
    # One backup for the run, not one per promoted entry. `backup_path_for` never
    # reuses a name and `update_bib_entry` copies the whole file, so computing it
    # inside the loop left a full copy of the library per `--replace` promotion:
    # against the 15.8 MB library configured here, promoting 100 preprints wrote
    # roughly 1.6 GB of `.bak` files that nothing ever cleans up. The first write
    # takes it — under its lock, immediately before writing, as `delete` and
    # `library merge` do — and the rest pass None.
    run_backup = _RunBackup(bib["path"])

    # Phase 1 decides every promotion with no lock held, because deciding one
    # means going out to the network: candidate discovery calls rate-limited
    # providers (Semantic Scholar is gated at 6 s per request) and attaching the
    # PDF downloads a file. `bib_repository.LOCK_TIMEOUT_SECONDS` is 300, so a
    # sweep that did that work while holding the run's exclusive bib lock made a
    # concurrent `pzi add` — the browser extension talking to `pzi server` —
    # *fail* rather than wait. Both modes collect their writes here and a
    # session applies them in chunks.
    pending: list[tuple[int, _PendingWrite]] = []
    #: One breaker for the run. Five providers over 13,462 candidates means a
    #: provider that has stopped answering costs a timeout 13,462 times for an
    #: answer already known.
    breaker = ProviderBreaker()
    #: Candidates this run is willing to check. `promote` walks 60% of a 22k
    #: library, at a per-candidate cost set by the slowest provider's polite
    #: interval, so an unbounded run is hours. `remaining` is reported so a
    #: bounded pass reads as resumable rather than as silently incomplete.
    budget = limit if limit is not None and limit >= 0 else None

    def _skip_reason(record: NormalizedRecord) -> str | None:
        """Why this record is not a candidate for this run, or None if it is.

        One predicate, deliberately. The count taken before the loop and the
        decision taken inside it were two copies of the same conditions, so a
        new skip rule had to land in both — and if it landed in one, the
        progress denominator disagreed with the work actually done, silently.
        """
        if not isinstance(record.get("citekey"), str):
            return "no_citekey"
        # `has_preprint_identity`, not `is_preprint`: the latter calls any
        # record without a `venue` a preprint, which is a large share of an
        # ordinary library, so promotion forked a second entry out of plain
        # @articles that merely lacked a `journal` field — manufacturing the
        # duplicates `pzi library dedupe` exists to report. `update_service`
        # refuses `is_preprint` here for exactly this reason and says so at its
        # own call site; the two commands now agree.
        if not has_preprint_identity(record):
            return "not_preprint"
        if mark_resolved and _RESOLVED_TAG in (record.get("tags") or []):
            # Already promoted on a previous --mark-resolved run.
            return "skipped_already_resolved"
        if promote_ledger.is_recently_checked(
            ledger,
            bib["name"],
            str(record.get("citekey")),
            now=ledger_now,
            horizon_days=recheck_after_days,
        ):
            # Asked inside the horizon and the answer was "not published yet".
            return "skipped_recently_checked"
        return None

    # Counted before the loop, not accumulated inside it. `on_item` reports
    # `done/total`, and a total that grows as the loop runs is not a denominator
    # — it made every progress line read `1/1`, `2/2`, and never reached the
    # threshold that would have printed one.
    eligible_total = sum(1 for record in records if _skip_reason(record) is None)
    planned = eligible_total if budget is None else min(budget, eligible_total)
    eligible = 0
    started = time.monotonic()

    def _flush_pending(queue: list[tuple[int, _PendingWrite]]) -> None:
        """Write everything queued, in one session, and empty the queue.

        One session per chunk rather than one per promotion: each takes the
        lock, re-reads the file, re-parses every entry and re-serialises the
        library, so a sweep that promoted K preprints did K full
        parse-and-serialise cycles over a 15.8 MB, 22,232-entry library. On a
        synthetic 4,010-entry library, 10 promotions took 7.4 s with a session
        each and 1.0 s with one between them, writing the same bytes either way.

        The session is opened only if something is queued: opening it eagerly
        took the lock and refused a malformed library on runs that were never
        going to write anything, and a sweep that skips every preprint must
        still report its skips.
        """
        if not queue:
            return
        #: Item indices whose write landed, so their backup path can be filled in
        #: once the session has actually written it.
        written: list[int] = []
        with ExitStack() as chunk_writes:
            session: BatchWriteSession | None = None
            for item_index, write in queue:
                try:
                    if session is None:
                        # Opened inside the try, so a library that cannot be
                        # rewritten at all — a malformed block anywhere in the
                        # file — is reported as these promotions failing rather
                        # than raised through the whole run.
                        session = chunk_writes.enter_context(
                            batch_write_session(
                                bib["path"],
                                file_path_style=file_path_style,
                                # Only the first session of the run receives a
                                # path — `take()` yields it once — so the `.bak`
                                # is the library as it stood before the run,
                                # which is the state an undo wants. Copied
                                # inside the session's lock, immediately before
                                # the write.
                                backup_path=run_backup.take(),
                            )
                        )
                    write.apply(session)
                except (StalePlanError, ConcurrentEditError):
                    raise  # see the carve-out in phase 1
                except _PreprintVanished as exc:
                    # Nothing was written and nothing was lost; the target is
                    # simply gone. Reported as that rather than as a failure.
                    _remove_new_pdf(
                        _local_pdf_path(write.record), write.existing_pdf_paths
                    )
                    items[item_index] = _promote_item(
                        write.preprint_ck, write.preprint_ck, "error", note=str(exc)
                    )
                    continue
                except Exception as exc:  # one failing write must not abort the run
                    _remove_new_pdf(
                        _local_pdf_path(write.record), write.existing_pdf_paths
                    )
                    summary["skipped_failed"] += 1
                    items[item_index] = _skip_item(
                        write.preprint_ck, f"promotion failed: {exc}", failed=True
                    )
                    continue
                summary[write.counter] += 1
                # Keep mode leaves the preprint in place, so tag it for a later
                # `--mark-resolved` run. Replace mode rewrote the entry into the
                # published version, which is no longer a preprint at all.
                if keep_preprint:
                    resolved_preprints.append(write.preprint_ck)
                else:
                    written.append(item_index)
        # Only now: the session writes on exit, so inside the block above the
        # `.bak` does not exist yet and `run_backup.path` reads as None.
        backup = run_backup.path
        if backup is not None:
            # Every promoted entry reports the same path — it is the one file
            # that undoes the run.
            for item_index in written:
                items[item_index]["backup_path"] = str(backup)
        queue.clear()

    def note_still_unpublished(citekey: str, provider_errors: list[str]) -> None:
        """Record a negative answer for the ledger — if it is really an answer.

        A provider error means the search was incomplete, so "found nothing" is
        not a finding and must not suppress the next sweep. This covers the
        breaker too: a provider it skipped appends to `provider_errors`, so an
        outage cannot freeze into a month of silence over exactly the entries
        this command exists to surface.
        """
        if provider_errors:
            return
        checked_negative.append(citekey)

    def record_item(item: PromoteItem) -> None:
        """Append a verdict and stream it, in one place.

        Five paths reach a verdict; routing them all through here is what stops
        `on_item` quietly missing one — and a caller that streams to disk is the
        only reason an interrupted sweep keeps its work.
        """
        items.append(item)
        if on_item is not None:
            on_item(item, len(items), planned)

    for record in records:
        reason = _skip_reason(record)
        if reason is not None:
            if reason in _COUNTED_SKIPS:
                summary[reason] += 1
            continue
        # Narrowing for the type checker; `_skip_reason` already rejected the
        # records where this is not a string.
        preprint_ck = record.get("citekey")
        if not isinstance(preprint_ck, str):
            continue
        # Eligible whether or not this run has budget left, so `remaining` counts
        # what a follow-up run would still face rather than what this loop saw.
        eligible += 1
        if budget is not None and summary["checked"] >= budget:
            continue
        summary["checked"] += 1

        candidate_result = find_published_candidate_with_diagnostics(
            record=record,
            server_url=config["translation_server_url"],
            fetch_search=fetch_search,
            fetch_crossref=fetch_crossref,
            fetch_openalex=fetch_openalex,
            fetch_dblp=fetch_dblp,
            fetch_openreview=fetch_openreview,
            fetch_s2=fetch_s2,
            s2_api_key=s2_api_key,
            contact_email=contact_email,
            metadata_fetch_text=metadata_fetch_text,
            breaker=breaker,
            best_of=best_of,
            gate=gate,
        )
        candidate = candidate_result["candidate"]
        provider_errors = candidate_result["provider_errors"]
        metadata_diagnostics = candidate_result.get("metadata_diagnostics", [])
        if candidate is None:
            if candidate_result.get("reason") == "no_query":
                continue
            summary["provider_errors"] += len(provider_errors)
            summary["skipped_no_candidate"] += 1
            note_still_unpublished(preprint_ck, provider_errors)
            note = "no published candidate found"
            if provider_errors:
                note = f"{note} (provider errors: {', '.join(provider_errors)})"
            item = _skip_item(preprint_ck, note)
            if metadata_diagnostics:
                item["metadata_diagnostics"] = metadata_diagnostics
            record_item(item)
            continue

        # Gate on the explainable 0–100 breakdown, not a coarse feature count:
        # the old integer scale let three shared surnames reach the threshold on
        # their own, so a candidate flagged `title_mismatch` was written in
        # anyway while the diagnostics printed `confidence 0/100`.
        match = score_match(record, candidate)
        score = match["score"]
        title_similarity = match["title_similarity"]
        if score < effective_confidence_threshold or title_similarity < _MIN_TITLE_SIMILARITY:
            summary["skipped_low_confidence"] += 1
            note_still_unpublished(preprint_ck, provider_errors)
            reason = (
                f"low confidence ({score} < {effective_confidence_threshold})"
                if score < effective_confidence_threshold
                else f"title too different ({title_similarity} < {_MIN_TITLE_SIMILARITY}) "
                "— looks like a related paper, not this one's published version"
            )
            item = _skip_item(preprint_ck, reason)
            if metadata_diagnostics:
                item["metadata_diagnostics"] = metadata_diagnostics
            metadata_warnings = published_candidate_confidence_warnings(
                score=score, min_score=effective_confidence_threshold
            )
            if metadata_warnings:
                item["metadata_warnings"] = metadata_warnings
            record_item(item)
            continue

        duplicate_ck = _find_duplicate_citekey(candidate, known_records, preprint_ck)
        if duplicate_ck is not None:
            msg = f"already exists as {duplicate_ck}"
            summary["skipped_existing"] += 1
            item = _skip_item(preprint_ck, msg, published_ck=duplicate_ck)
            if metadata_diagnostics:
                item["metadata_diagnostics"] = metadata_diagnostics
            record_item(item)
            continue

        # Explainable breakdown of the score the gate above accepted (shown
        # under --verbose).
        match_line = (
            f"match confidence {match['score']}/100 "
            f"(title {match['title_similarity']}, author {match['author_similarity']})"
        )
        if match["flags"]:
            match_line += f"; flags: {', '.join(match['flags'])}"
        metadata_diagnostics = [match_line, *metadata_diagnostics]

        pdf_kwargs: dict[str, Any] = dict(
            papers_dir=bib["papers_dir"],
            fetch_binary=fetch_binary,
            flaresolverr_url=effective_flaresolverr_url,
            browser_pdf_cmd=effective_browser_pdf_cmd,
            pdf_filename_format=config.get("pdf_filename_format"),
            browser_hook=config.get("browser_hook", True),
            # Built once for the run: it resolves credentials, which do not vary
            # per entry. Without it `promote` retries one URL through every
            # transport and gives up, the same defect `add` and `retry` had.
            next_candidate=next_pdf_candidate_for_config(config, bib),
        )

        # Isolate the handler for one preprint: an unexpected failure here
        # (malformed candidate, failed download) must surface as an explainable
        # skip and let the rest of the library promote, not abort the whole run.
        # The handlers clean up any downloaded PDF before raising.
        try:
            if keep_preprint:
                item, write = _handle_keep_preprint(
                    bib_path=bib["path"],
                    preprint_record=record,
                    candidate=candidate,
                    existing_citekeys=existing_citekeys,
                    dry_run=dry_run,
                    citekey_format=config.get("citekey_format"),
                    file_path_style=file_path_style,
                    **pdf_kwargs,
                )
            else:
                item, write = _handle_update_in_place(
                    bib_path=bib["path"],
                    preprint_record=record,
                    candidate=candidate,
                    dry_run=dry_run,
                    file_path_style=file_path_style,
                    **pdf_kwargs,
                )
        except (StalePlanError, ConcurrentEditError):
            # Not a per-record problem: the bib changed underneath this run, so
            # continuing to write the remaining preprints is unsafe. Swallowed,
            # one lost write race was reported once per preprint as "promotion
            # failed" on a run that still called itself ok. `update_bib` makes
            # the same carve-out; the CLI and the HTTP API both classify these
            # at their boundary.
            raise
        except Exception as exc:  # one failing entry must not abort the run
            summary["skipped_failed"] += 1
            record_item(
                _skip_item(preprint_ck, f"promotion failed: {exc}", failed=True)
            )
            continue
        if metadata_diagnostics:
            item["metadata_diagnostics"] = metadata_diagnostics

        record_item(item)
        if write is not None:
            # Counted when the write lands, not here: whether this promotion
            # happened is not known until its edits have gone into a session.
            pending.append((len(items) - 1, write))
            if len(pending) >= _WRITE_CHUNK:
                _flush_pending(pending)
        # Both of these feed the *next* record's decisions — the citekey
        # generator and `_find_duplicate_citekey` — so they are recorded as soon
        # as this record resolves, before phase 2 knows whether the write lands.
        # The alternative is a sweep that forks two entries for one paper.
        if item["published_citekey"] is not None:  # pragma: no branch
            existing_citekeys.add(item["published_citekey"])
            if item["action"] in {"create", "update"}:
                published_record = dict(candidate)
                published_record["citekey"] = item["published_citekey"]
                known_records.append(published_record)

    # Phase 2 — whatever the last chunk left over. The writes are the only part
    # that holds the lock, and they make no network call.
    #
    # Only the write path reaches here: the dry run needs a per-preprint diff,
    # and the only way to render one is `preview_batch_write`, which opens the
    # file itself.
    _flush_pending(pending)

    # Derived from the item outcomes rather than accumulated beside them, as
    # `update_bib` derives its own: a verdict is settled in phase 1 for a skip
    # and in phase 2 for a write, and reading them back off `items` keeps the
    # errors in record order whichever phase produced them.
    errors = [
        f"{item['preprint_citekey']}: {item['note']}"
        for item in items
        if item.get("failed")
    ]
    # A provider dropped for the rest of the run is reported once, not per
    # candidate — the same rule `check` applies to the same providers.
    errors.extend(breaker.tripped.values())

    # Persist the negatives once, not per item, and *including* under
    # `--dry-run`: the lookups really happened and "still unpublished" is just
    # as true for a preview, while the sidecar is not the library, so a dry run
    # stays read-only against the `.bib`. Pruning here is what bounds the file.
    if recheck_after_days > 0 and checked_negative:
        for citekey in checked_negative:
            ledger = promote_ledger.record_checked(
                ledger, bib["name"], citekey, now=ledger_now
            )
        promote_ledger.save(
            ledger_file,
            promote_ledger.prune(
                ledger, now=ledger_now, horizon_days=recheck_after_days
            ),
        )

    # What a follow-up run still faces, and what this one cost per candidate.
    # `remaining` is the difference between a bounded pass and a silently
    # incomplete one. The timing is not decoration: the default bound is chosen
    # from it (PLAN.md section F, Step 3), and item 577's worth is judged against
    # the resolve rate, so both are reported rather than guessed at later.
    summary["eligible"] = eligible_total
    summary["remaining"] = max(0, eligible_total - summary["checked"])
    elapsed = time.monotonic() - started
    summary["elapsed_seconds"] = round(elapsed, 1)
    if summary["checked"]:
        summary["seconds_per_candidate"] = round(elapsed / summary["checked"], 2)
        resolved = summary["checked"] - summary["skipped_no_candidate"]
        summary["resolve_rate"] = round(resolved / summary["checked"], 3)

    # Emit a top-level warning when S2 rate-limit failures accumulate.
    s2_rate_count = sum(
        1 for item in items
        if isinstance(item["note"], str)
        and "semantic-scholar (rate" in item["note"]
    )
    if s2_rate_count >= 2:
        key_configured = bool(
            resolve_optional_value(
                command=config.get("semantic_scholar_api_key_cmd"),
                fallback=config.get("semantic_scholar_api_key"),
            )
        )
        if not key_configured:
            summary["s2_warning"] = (
                f"{s2_rate_count} Semantic Scholar rate-limit failures. "
                "Configure semantic_scholar_api_key_cmd in config.toml for higher limits."
            )

    if mark_resolved and not dry_run and resolved_preprints:
        tagged, tag_failures = _tag_resolved(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekeys=resolved_preprints,
        )
        summary["marked_resolved"] = tagged
        errors.extend(tag_failures)

    # A run where *every* preprint failed is not `ok`. Derived, as `update_bib`
    # and `check_bib` derive theirs: hardcoded `ok` here meant `pzi.promote()`
    # returned normally and `POST /promote` answered 200 for a run that promoted
    # nothing. A *partial* failure stays `ok` and is reported through `errors`
    # and each item's `failed`, which is what the CLI runner turns into PARTIAL.
    all_failed = bool(items and all(item.get("failed") for item in items))
    result: PromoteResult = {
        "status": "error" if all_failed else "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "keep_preprint": keep_preprint,
        "items": items,
        "summary": summary,
        # Populated from the per-item failures above. Hardcoding `[]` meant a
        # run in which every promotion raised reported no errors at all.
        "errors": errors,
    }
    if all_failed:
        # `unavailable`, not `config`: the library was read and the preprints
        # were there — the promotions were not writable. Both
        # `exit_code_for_error` and `http_status` map it, so an unclassified
        # failure cannot fall back to 400.
        result["reason"] = REASON_UNAVAILABLE
    return result


def _empty_summary() -> dict[str, Any]:
    return {
        "checked": 0,
        "created": 0,
        "updated": 0,
        "skipped_no_candidate": 0,
        "skipped_low_confidence": 0,
        "skipped_existing": 0,
        "skipped_already_resolved": 0,
        "skipped_recently_checked": 0,
        "skipped_failed": 0,
        "provider_errors": 0,
    }


def _tag_resolved(
    *, config_path: str, home_dir: str, bib_selector: str | None, citekeys: list[str]
) -> tuple[int, list[str]]:
    """Tag each promoted preprint with the resolved marker.

    Returns how many were actually tagged and what failed. Every result used to
    be discarded as "best-effort" while the caller set
    `marked_resolved = len(citekeys)` unconditionally — so a failed tag write
    reported success, the marker was absent, and the next run promoted the same
    preprint again. Best-effort is the right policy; claiming it worked is not.
    """
    tagged = 0
    failures: list[str] = []
    for citekey in citekeys:
        result = add_tags(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=citekey,
            tags=[_RESOLVED_TAG],
        )
        if result.get("status") == "ok":
            tagged += 1
        else:
            failures.append(
                f"{citekey}: could not mark resolved "
                f"({result.get('message') or 'tag write failed'})"
            )
    return tagged, failures


# ---------------------------------------------------------------------------
# Deduplication (candidate scoring lives in `promote_planning`)
# ---------------------------------------------------------------------------


def _find_duplicate_citekey(
    candidate: NormalizedRecord,
    records: list[NormalizedRecord],
    exclude_citekey: str,
) -> str | None:
    # Canonical comparison on both sides. Raw equality missed `10.1145/ABC` vs
    # `10.1145/abc` and `"Deep  Residual Learning"` (double space) vs
    # `"Deep Residual Learning"` — and the write that follows uses
    # `force_new=True`, so nothing downstream catches the duplicate it creates.
    c_doi = canonical_doi(candidate.get("doi"))
    c_title = normalize_title(candidate.get("title"))
    for rec in records:
        ck = rec.get("citekey")
        if not isinstance(ck, str) or ck == exclude_citekey:
            continue
        if c_doi and canonical_doi(rec.get("doi")) == c_doi:
            return ck
        if c_title and normalize_title(rec.get("title")) == c_title:
            return ck
    return None


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _promote_item(
    preprint_citekey: str,
    published_citekey: str | None,
    action: str,
    *,
    changed_fields: list[str] | None = None,
    pdf_attached: bool | None = False,
    note: str | None = None,
    diff: str | None = None,
    backup_path: str | None = None,
) -> PromoteItem:
    """Build a PromoteItem dict with all standard fields."""
    item: PromoteItem = {
        "preprint_citekey": preprint_citekey,
        "published_citekey": published_citekey,
        "action": action,
        "changed_fields": changed_fields or [],
        "pdf_attached": pdf_attached,
        "note": note,
    }
    if diff is not None:
        item["diff"] = diff
    if backup_path is not None:
        item["backup_path"] = backup_path
    return item


def _skip_item(
    preprint_ck: str,
    note: str,
    published_ck: str | None = None,
    *,
    failed: bool = False,
) -> PromoteItem:
    item = _promote_item(preprint_ck, published_ck, "skip", note=note)
    if failed:
        item["failed"] = True
    return item


class _PreprintVanished(Exception):
    """The entry to promote was gone by the time the write session opened.

    Distinct from an ordinary failed write: nothing was malformed and nothing
    was lost, the target simply is not there any more, so it is reported as
    that rather than counted as a failure.
    """


@dataclass
class _PendingWrite:
    """A promotion decided outside the lock, waiting for a write session.

    Deciding one costs network — discovery, then the PDF download — while a
    write session holds the bib's exclusive lock. So the two are kept apart:
    the handlers return one of these instead of writing, and `promote_bib`
    folds them into sessions in chunks.

    Both modes produce these. They differ only in what `apply` folds into the
    session — an insert plus a cross-reference note, or an in-place update —
    so the run has one write path to schedule, count and recover rather than
    two that drift.
    """

    preprint_ck: str
    #: The record whose freshly downloaded PDF is removed if the write never
    #: lands, so a rollback deletes what this promotion pulled in and nothing
    #: else.
    record: NormalizedRecord
    existing_pdf_paths: set[Path]
    #: The summary counter this promotion increments once its write has landed.
    counter: str
    #: Folds this promotion's edits into an open session.
    apply: Callable[[BatchWriteSession], None]


def _handle_keep_preprint(
    *,
    bib_path: str,
    preprint_record: NormalizedRecord,
    candidate: NormalizedRecord,
    existing_citekeys: set[str],
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
    pdf_filename_format: str | None = None,
    citekey_format: str | None = None,
    browser_hook: bool = True,
    file_path_style: str = "absolute",
    next_candidate: NextPdfCandidate | None = None,
) -> tuple[PromoteItem, _PendingWrite | None]:
    """The item this promotion produces, and the write it still owes the run.

    A dry run renders its diff here and owes nothing; a real run owes a
    `_PendingWrite`, because every write is deferred to a session.
    """
    preprint_ck = cast(str, preprint_record.get("citekey", ""))

    published = _merge_published_metadata(preprint_record, candidate)
    published_ck = _generate_citekey_for_candidate(
        published,
        existing_citekeys,
        citekey_format=citekey_format,
    )
    published["citekey"] = published_ck
    # The new entry's own cross-reference is known before it is written, so it
    # goes into the record rather than a follow-up update — one fewer write that
    # can leave the pair half-applied.
    back_reference = _append_note(published.get("note"), f"Preprint version: {preprint_ck}")
    if back_reference is not None:
        published["note"] = back_reference

    existing_pdf_paths = _snapshot_pdf_paths(papers_dir)
    published, pdf_attached = _maybe_attach_pdf(
        published,
        published_ck,
        dry_run,
        papers_dir,
        fetch_binary,
        flaresolverr_url,
        browser_pdf_cmd,
        pdf_filename_format,
        browser_hook=browser_hook,
        next_candidate=next_candidate,
    )

    # Diffed against the *preprint*, which is what the promotion changes — not
    # against the candidate, which listed the fields the candidate did not
    # supply and called them changes.
    changed_fields = changed_fields_between(preprint_record, published) or ["venue", "doi"]

    diff: str | None = None
    pending: _PendingWrite | None = None
    if dry_run:
        # Same `force_new` as the real write, so the preview shows the insert
        # the run will actually perform rather than a spurious in-place update.
        # Both writes, through the same builder the real run uses: the preview
        # used to show only the insert and omit the cross-reference note
        # stamped onto the preprint.
        diff = preview_batch_write(
            bib_path,
            lambda preview: _apply_published_fork(
                preview, published, preprint_ck, published_ck,
                bib_path=bib_path, check_collision=False,
            ),
            file_path_style=file_path_style,
        )["diff"]
    else:
        pending = _PendingWrite(
            preprint_ck=preprint_ck,
            record=published,
            existing_pdf_paths=existing_pdf_paths,
            counter="created",
            apply=lambda session: _apply_published_fork(
                session, published, preprint_ck, published_ck,
                bib_path=bib_path, check_collision=True,
            ),
        )

    return _promote_item(
        preprint_ck, published_ck, "create",
        changed_fields=changed_fields,
        pdf_attached=pdf_attached,
        diff=diff,
    ), pending


def _relocate_venue_for_entry_type(entry: BibtexEntry) -> BibtexEntry:
    """Move the venue to the key *entry*'s type expects.

    `merge_projected_entry` writes the venue back to whichever key the entry
    already used, because an ordinary update must not rewrite a proceedings
    entry's `booktitle` as `journal`. Promotion is the case where the type
    itself changes, so the venue's home has to follow it — otherwise a workshop
    paper promoted to a journal ends up an `@article` whose journal name sits in
    `booktitle`. Mutates and returns *entry*, whose ``fields`` the callers own.

    Only the retype-on-merge path needs this: fresh inserts go through
    `record_to_bibtex_entry`, which already picks the venue key from the entry
    type.
    """
    fields = entry["fields"]
    wanted = venue_field_for_entry_type(entry["entry_type"])
    stale = "journal" if wanted == "booktitle" else "booktitle"
    # Only relocate when the target is free: an entry carrying both keys is
    # already ambiguous, and dropping either would lose a venue.
    if stale in fields and wanted not in fields:
        fields[wanted] = fields.pop(stale)
    return entry


def _apply_published_fork(
    session: BatchWriteSession,
    published: NormalizedRecord,
    preprint_ck: str,
    published_ck: str,
    *,
    bib_path: str,
    check_collision: bool,
) -> None:
    """Fold both of the fork's writes into *session*.

    Shared by the real write and the dry-run preview, so the preview cannot
    show a different pair of writes than the run performs. Both edits go into
    one session so the pair cannot half-apply: the old sequence committed the
    insert first and stamped the preprint's note after, and only the note path
    refuses to patch a library containing a malformed block — so one broken
    entry anywhere in the file left the published entry committed (with a
    ``file =`` pointing at the PDF the rollback had just deleted) behind a
    reported ``created 0``.
    """
    if check_collision and any(
        record.get("citekey") == published_ck for record in session.records
    ):
        # `published_ck` was generated against the snapshot read at the top of
        # the run, but this session re-reads under the lock. The single-write
        # path reconciles that drift in `_rebase_insert_plan_against_current`,
        # which a batch session does not go through — so the collision is
        # checked here, and with `force_new` set nothing else would. This is
        # the only `ConcurrentEditError` left in the tree: everywhere else, a
        # concurrent edit is absorbed rather than refused.
        raise ConcurrentEditError(
            f"citekey {published_ck} appeared in {bib_path} while promoting "
            f"{preprint_ck}; aborting rather than writing a duplicate entry"
        )
    # force_new: the published record is forked from the preprint and can
    # still share an identity with it, so an identity-matched plan would
    # turn this insert into an in-place update of the preprint itself. The
    # candidate was already checked against the library by
    # `_find_duplicate_citekey`, so there is nothing legitimate to match.
    session.apply_plan(plan_bib_write(published, session.records, force_new=True))
    note_plan = _plan_note_update(
        session, preprint_ck, f"Published version: {published_ck}"
    )
    if note_plan is not None:
        session.apply_plan(note_plan)


def _plan_note_update(
    session: BatchWriteSession, citekey: str, text: str
) -> WritePlan | None:
    """Plan a note append for *citekey*, or None when there is nothing to do."""
    for index, record in enumerate(session.records):
        if record.get("citekey") != citekey:
            continue
        new_note = _append_note(record.get("note"), text)
        if new_note is None:
            return None
        updated = cast(NormalizedRecord, {**record, "note": new_note})
        return {
            "action": "update",
            "index": index,
            "record": updated,
            # Pre-merged onto the entry on disk, like every other update plan:
            # both sinks apply `plan["entry"]` verbatim, so a bare projection
            # here would drop every field the record model does not carry.
            "entry": merge_projected_entry(
                session.entries[index],
                record_to_bibtex_entry(
                    updated, entry_type=session.entries[index]["entry_type"]
                ),
            ),
            "changed_fields": ["note"],
        }
    return None


def _apply_in_place_promotion(
    session: BatchWriteSession,
    preprint_ck: str,
    candidate: NormalizedRecord,
    *,
    pdf_source: NormalizedRecord,
) -> None:
    """Fold the in-place rewrite of *preprint_ck* into *session*.

    The published metadata is merged onto the record as this session read it
    under the lock, not onto the snapshot taken before discovery ran — which is
    the property `update_bib_entry` used to provide by re-reading, and the
    reason a concurrent edit is absorbed rather than overwritten.
    """
    for index, record in enumerate(session.records):
        if record.get("citekey") != preprint_ck:
            continue
        entry = _promoted_entry(
            session.entries[index], record, candidate, pdf_source=pdf_source
        )
        updated = bibtex_entry_to_record(entry)
        session.apply_plan(
            {
                "action": "update",
                "index": index,
                "record": updated,
                # Authoritative, like every other update plan: the entry is
                # already merged onto the one on disk, so both sinks apply it
                # verbatim rather than re-deriving it from the projection.
                "entry": entry,
                "changed_fields": changed_fields_between(record, updated),
            }
        )
        return
    raise _PreprintVanished(
        "preprint entry disappeared before promotion update could be written"
    )


def _promoted_entry(
    entry: BibtexEntry,
    current_record: NormalizedRecord,
    candidate: NormalizedRecord,
    *,
    pdf_source: NormalizedRecord | None = None,
) -> BibtexEntry:
    """The entry a promotion writes in place. Shared by the write and the preview.

    Merges the published metadata onto the record as it is on disk *now*, not
    onto the pre-lock snapshot: `update_bib_entry` re-reads under the exclusive
    lock precisely so a concurrent edit is not thrown away.
    """
    locked = _merge_published_metadata(
        cast(NormalizedRecord, dict(current_record)), candidate
    )
    # The PDF is acquired before the lock is taken, so its path lives on the
    # pre-lock record and has to be carried over onto the fresh merge.
    for pdf_field in ("local_pdf_path", "pdf_url"):
        value = (pdf_source or {}).get(pdf_field)
        if value:
            locked[pdf_field] = value  # type: ignore[literal-required]
    # Project onto the entry rather than replacing it: a rebuild from the record
    # drops every field `NormalizedRecord` does not model (`volume`, `pages`,
    # `publisher`, ...).
    projected = record_to_bibtex_entry(locked, entry_type=resolve_entry_type(locked))
    merged = merge_projected_entry(entry, projected)
    # `merge_projected_entry` keeps the on-disk entry type, because an ordinary
    # update has no business retyping an entry. Promotion is the exception — the
    # preprint has become a published paper — so the type is resolved from the
    # promoted record rather than left as `@unpublished`.
    if projected["entry_type"] != merged["entry_type"]:
        merged["entry_type"] = projected["entry_type"]
        _relocate_venue_for_entry_type(merged)
    return merged


def _preview_in_place_update(
    bib_path: str,
    preprint_ck: str,
    candidate: NormalizedRecord,
    *,
    file_path_style: str,
) -> str | None:
    """The diff `--replace` will produce, built from the same entry projection."""
    read_result = read_bib_file(bib_path)
    entries = read_result["entries"]
    records = read_result["records"]
    index = next(
        (i for i, entry in enumerate(entries) if entry["citekey"] == preprint_ck),
        None,
    )
    if index is None:  # pragma: no cover — the caller just read this citekey
        return None
    merged_entry = _promoted_entry(entries[index], records[index], candidate)
    plan: WritePlan = {
        "action": "update",
        "index": index,
        "record": bibtex_entry_to_record(merged_entry),
        "entry": merged_entry,
        "changed_fields": [],
    }
    return preview_write_plan(bib_path, plan, file_path_style=file_path_style)["diff"]


class _RunBackup:
    """The single ``.bak`` a promote run leaves, taken by whichever write is first.

    ``update_bib_entry`` copies the whole bib to the path it is given, so passing
    a fresh path per entry meant a full copy per promotion, and passing the *same*
    path per entry would overwrite the original with an already-promoted state.
    Handing the path to the first write only gets both right: one copy, of the
    library as it was before the run.
    """

    def __init__(self, bib_path: str) -> None:
        self._bib_path = bib_path
        self._path: Path | None = None
        self._taken = False

    def take(self) -> Path | None:
        """The backup path for the next write, or None once one has been taken."""
        if self._taken:
            return None
        self._taken = True
        self._path = backup_path_for(self._bib_path, "promote")
        return self._path

    @property
    def path(self) -> Path | None:
        """The backup that was written, or None if nothing was backed up."""
        if self._path is not None and self._path.exists():
            return self._path
        return None


def _handle_update_in_place(
    *,
    bib_path: str,
    preprint_record: NormalizedRecord,
    candidate: NormalizedRecord,
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
    pdf_filename_format: str | None = None,
    browser_hook: bool = True,
    file_path_style: str = "absolute",
    next_candidate: NextPdfCandidate | None = None,
) -> tuple[PromoteItem, _PendingWrite | None]:
    """The item this promotion produces, and the write it still owes the run.

    Symmetrical with `_handle_keep_preprint`: the PDF download happens here,
    outside the lock, and the entry rewrite is deferred to a write session.
    It used to write here through `update_bib_entry`, taking the lock and
    re-parsing all 22,232 entries once per promoted preprint.
    """
    preprint_ck = cast(str, preprint_record.get("citekey", ""))

    updated = _merge_published_metadata(preprint_record, candidate)
    updated["citekey"] = preprint_ck

    pdf_attached = False
    diff: str | None = None
    pending: _PendingWrite | None = None
    if dry_run:
        # Target the preprint *by citekey*, exactly as the real write does with
        # `update_bib_entry`. Relying on identity matching produced an INSERT
        # here — `_merge_published_metadata` strips `arxiv_id`, so nothing
        # matched — and the preview told the user their original entry survived
        # when `--replace` overwrites it.
        diff = _preview_in_place_update(
            bib_path,
            preprint_ck,
            candidate,
            file_path_style=file_path_style,
        )
    else:
        existing_pdf_paths = _snapshot_pdf_paths(papers_dir)
        updated, pdf_attached = _maybe_attach_pdf(
            updated,
            preprint_ck,
            dry_run,
            papers_dir,
            fetch_binary,
            flaresolverr_url,
            browser_pdf_cmd,
            pdf_filename_format,
            browser_hook=browser_hook,
            next_candidate=next_candidate,
        )

        pending = _PendingWrite(
            preprint_ck=preprint_ck,
            record=updated,
            existing_pdf_paths=existing_pdf_paths,
            counter="updated",
            apply=lambda session: _apply_in_place_promotion(
                session, preprint_ck, candidate, pdf_source=updated
            ),
        )

    # Over the union of both key sets: iterating `updated` alone could only ever
    # report fields that survived, so a field the promotion *removed* — the
    # `eprint`, the preprint URL, the arXiv DOI — was applied but never named.
    changed_fields = changed_fields_between(preprint_record, updated)

    # `backup_path` is filled in once the session that writes this promotion has
    # taken the run's backup — see `_flush_pending`.
    return _promote_item(
        preprint_ck, preprint_ck, "update",
        changed_fields=changed_fields,
        pdf_attached=pdf_attached if not dry_run else None,
        diff=diff,
    ), pending


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _maybe_attach_pdf(
    record: NormalizedRecord,
    citekey: str,
    dry_run: bool,
    papers_dir: str,
    fetch_binary,
    flaresolverr_url: str | None,
    browser_pdf_cmd: str | None,
    pdf_filename_format: str | None = None,
    browser_hook: bool = True,
    next_candidate: NextPdfCandidate | None = None,
) -> tuple[NormalizedRecord, bool]:
    pdf_url = record.get("pdf_url")
    if not isinstance(pdf_url, str) or not pdf_url.strip() or dry_run:
        return record, False

    outcome = fetch_and_store_pdf_trying_sources(
        url=pdf_url,
        record=record,
        next_candidate=next_candidate,
        papers_dir=papers_dir,
        citekey=citekey,
        fetch_binary=fetch_binary,
        flaresolverr_url=flaresolverr_url,
        browser_pdf_cmd=browser_pdf_cmd,
        browser_hook=browser_hook,
        filename_format=pdf_filename_format,
    )
    if outcome.local_pdf_path is None:
        return record, False

    # `outcome.record`, so a promotion that fell back to another source records
    # the URL that produced the file rather than the one that failed.
    updated = dict(outcome.record)
    updated["local_pdf_path"] = outcome.local_pdf_path
    return cast(NormalizedRecord, updated), True


def _local_pdf_path(record: NormalizedRecord) -> str | None:
    path = record.get("local_pdf_path")
    return path if isinstance(path, str) else None


def _empty_to_none_str(value: object) -> str | None:
    """A non-blank string, or None — so a blank `arxiv_id` is not kept as one."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _merge_published_metadata(
    preprint: NormalizedRecord, candidate: NormalizedRecord,
) -> NormalizedRecord:
    merged = dict(preprint)
    for key, value in candidate.items():
        if key in USER_OWNED_FIELDS:
            continue
        # A candidate key the provider could not fill is *absent* metadata, not
        # an instruction to clear the preprint's. `_openreview_normalize` always
        # emits `doi: None`, so copying it blindly deletes a populated DOI.
        if value is None or value == "" or value == []:
            continue
        merged[key] = value
    merged["tags"] = list(preprint.get("tags") or [])

    # The result describes the *published* version, so it must not inherit the
    # preprint's identity. Beyond being wrong metadata, an inherited identity is
    # load-bearing twice over: `find_exact_match` keys on `canonical_url`, so a
    # kept URL makes the published insert collide with the preprint it came
    # from, and `resolve_entry_type` keys on the preprint hosts, so it would
    # type the published entry `@unpublished`.
    # Kept as a pointer before it goes, under a name `has_preprint_identity`
    # does not read (`pzi-preprint-arxiv-id` in the file). The published entry
    # should still say which preprint it came from — losing that was the one
    # thing `--replace` gave up — but restoring it as `arxiv_id` would re-select
    # the entry on every future sweep, which is the loop promotion ends.
    stripped_arxiv_id = _empty_to_none_str(merged.get("arxiv_id"))
    if stripped_arxiv_id is not None:
        merged["preprint_arxiv_id"] = stripped_arxiv_id
    merged.pop("arxiv_id", None)
    # `10.48550/arXiv.…` is arXiv's own DataCite DOI: it identifies the
    # *preprint*, so keeping it labels the published entry with the version it
    # just stopped being — and a later `pzi library check` resolves it straight back to
    # the preprint. Only dropped when the candidate offered no DOI of its own;
    # when it did, the loop above has already overwritten this.
    if is_preprint_doi(merged.get("doi")):
        merged.pop("doi", None)
    for url_field in ("canonical_url", "source_url"):
        if candidate.get(url_field):
            continue
        if is_preprint_url(merged.get(url_field)):
            merged.pop(url_field, None)
    # Better BibTeX writes `publisher = {arXiv}` and `number = {arXiv:2110.09348}`
    # onto its arXiv entries, and both rode the merge into the published record:
    # a paper promoted to ICLR claimed arXiv as its publisher and kept the
    # preprint's identifier as its issue number. Invisible until the rest of
    # this was fixed, because the "published" record used to *be* the arXiv one,
    # where those fields looked correct. Same rule as the DOI above — dropped
    # only when the candidate offered nothing of its own.
    if not candidate.get("publisher") and names_a_preprint_server(
        merged.get("publisher")
    ):
        merged.pop("publisher", None)
    for locator in ("number", "volume"):
        if not candidate.get(locator) and _is_arxiv_locator(merged.get(locator)):
            merged.pop(locator, None)
    return cast(NormalizedRecord, merged)


#: `arXiv:2110.09348` in `number`, `abs/2112.15246` in `volume` — arXiv's own
#: locators as Better BibTeX and DBLP write them. Anchored, so a real issue
#: number or volume cannot match.
_ARXIV_LOCATOR_RE = re.compile(r"^(?:arxiv:|abs/)", re.IGNORECASE)


def _is_arxiv_locator(value: object) -> bool:
    """True when a ``number``/``volume`` is really an arXiv identifier."""
    return isinstance(value, str) and bool(_ARXIV_LOCATOR_RE.match(value.strip()))


#: arXiv's DataCite prefix. Deliberately just this one: bioRxiv and medRxiv
#: share `10.1101/` with Cold Spring Harbor Laboratory Press's journals, so the
#: prefix alone cannot tell a preprint from a published paper there.


def _append_note(existing: object, text: str) -> str | None:
    """Append *text* to a note, or return None when it is already there."""
    note_str = str(existing) if existing is not None else ""
    if text in note_str:
        return None
    return f"{note_str}; {text}" if note_str else text


def _generate_citekey_for_candidate(
    record: NormalizedRecord,
    existing_citekeys: set[str],
    *,
    citekey_format: str | None = None,
) -> str:
    if citekey_format:
        return format_citekey(citekey_format, record, existing_citekeys)
    return generate_citekey(
        {"authors": normalize_authors(record.get("authors")),
         "title": cast(str | None, record.get("title")),
         "year": cast(int | None, record.get("year"))},
        existing_citekeys,
    )
