"""Update/enrichment workflow service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict, cast

from pzi.add_planning import (
    metadata_result_confidence_warnings,
    metadata_result_diagnostics,
    score_metadata_candidate,
    select_best_metadata_result,
)
from pzi.bib_repository import (
    BatchWriteSession,
    StalePlanError,
    WritePlan,
    batch_write_session,
    find_entry_index,
    preview_write_plan,
    read_bib_file,
    same_paper,
)
from pzi.bib_serialize import validate_bibtex_roundtrip
from pzi.bibtex import (
    USER_OWNED_FIELDS,
    BibtexEntry,
    NormalizedRecord,
    apply_record_to_entry,
    bibtex_entry_to_record,
    changed_fields,
)
from pzi.config import DEFAULT_PDF_FILE_PATH_STYLE, BibResolutionFailure, load_bib_target
from pzi.errors import REASON_CONFIG, REASON_UNAVAILABLE
from pzi.identifiers import has_preprint_identity
from pzi.protocols import SearchTranslationFetcher
from pzi.resolution_match import score_match
from pzi.similarity import canonical_doi, normalize_title
from pzi.translation_server import fetch_search_translations


class UpdatePlanItem(TypedDict):
    """One entry considered by an update, inside an `UpdateBibResult`.

    `applied` is False for every item of a dry run, so it is not a failure
    predicate — `failed` and `skipped` are.
    """

    citekey: str
    changed_fields: list[str]
    applied: bool
    note: str | None
    # Set when this record could not be updated. `note` alone cannot carry it:
    # it is also set for benign outcomes, and `applied` is False for *every*
    # item of a healthy `--dry-run`, so neither can serve as a failure
    # predicate. The runner needs one to report PARTIAL.
    failed: NotRequired[bool]
    #: Set when a candidate was found but refused (a contradicting DOI, or a
    #: score below `metadata_confidence_min_score`). Distinct from `failed`:
    #: nothing went wrong, the metadata was simply not good enough to write.
    skipped: NotRequired[bool]
    diff: NotRequired[str]
    metadata_diagnostics: NotRequired[list[str]]
    metadata_warnings: NotRequired[list[str]]


class UpdateBibResult(TypedDict):
    """An enrichment sweep — what `pzi.update()` returns.

    One `UpdatePlanItem` per entry the sweep considered. Previews by
    default; `dry_run` says which this was.
    """

    status: str
    bib_name: str | None
    dry_run: bool
    items: list[UpdatePlanItem]
    errors: list[str]
    #: Structured failure reason (`pzi.errors.REASON_*`) — present only on
    #: failure. Both the exit-code and HTTP-status mappers read it.
    reason: NotRequired[str]
#: Alias kept so this module's call site reads unchanged; the set itself is
#: shared with `promote` so the two commands cannot disagree about what
#: belongs to the user.
_USER_OWNED_UPDATE_FIELDS = USER_OWNED_FIELDS

_ENTRY_DISAPPEARED = "entry disappeared during update"

#: Enrichments written per bib lock. A write session parses and validates the
#: whole library once, so writing N updates one by one cost N parses (about
#: 3.5 s each on a 22,000-entry library). Chunking, rather than writing every
#: update at the end, keeps what is already looked up durable if a long run is
#: interrupted and bounds how long the lock is held. Lookups always happen
#: outside the lock.
_WRITE_BATCH = 50


@dataclass
class _PendingWrite:
    """A looked-up enrichment waiting for the next write session."""

    item: UpdatePlanItem
    citekey: str
    candidate: Mapping[str, Any]
    #: The record the lookup was made for. The entry under the lock must still
    #: be that paper, or the candidate's DOI and abstract land on another one.
    snapshot: Mapping[str, Any]
    #: Cleared when the write finds nothing left to change, so the run reports
    #: no item for it, exactly as a lookup that adds nothing does.
    keep: bool = True


def update_bib(
    *,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    dry_run: bool = True,
    fetch_search: SearchTranslationFetcher | None = None,
) -> UpdateBibResult:
    resolved = load_bib_target(
        config_path=config_path, home_dir=home_dir, bib_selector=bib_selector
    )
    if isinstance(resolved, BibResolutionFailure):
        return {
            "status": "error",
            "reason": REASON_CONFIG,
            "bib_name": None,
            "dry_run": dry_run,
            "items": [],
            "errors": resolved.errors,
        }
    config, bib = resolved
    search_fn = fetch_search or fetch_search_translations
    metadata_confidence_min_score = config["metadata_confidence_min_score"]
    file_path_style = config["pdf_file_path_style"]
    read_result = read_bib_file(bib["path"])
    records = read_result["records"]
    entries = read_result["entries"]

    items: list[UpdatePlanItem] = []
    queued: list[_PendingWrite] = []
    chunk: list[_PendingWrite] = []

    def flush() -> None:
        if chunk:
            _write_pending(bib["path"], chunk, file_path_style=file_path_style)
            chunk.clear()

    for record in records:
        citekey = record.get("citekey")
        if not isinstance(citekey, str):
            continue
        if not _needs_update(record):
            continue
        # Isolate each record: a malformed candidate or a mid-update failure
        # must not abort the whole pass.  (The lookup itself is already reported
        # as a per-item note inside the helper; this guards everything else.)
        try:
            item = _plan_update_for_record(
                cast(NormalizedRecord, record),
                citekey,
                bib_path=bib["path"],
                server_url=str(config["translation_server_url"]),
                search_fn=search_fn,
                records=cast("list[NormalizedRecord]", records),
                entries=entries,
                dry_run=dry_run,
                metadata_confidence_min_score=metadata_confidence_min_score,
                file_path_style=file_path_style,
            )
        except StalePlanError:
            # Not a per-record problem: the bib changed underneath this run, so
            # continuing to write the remaining records is unsafe. Every other
            # command surfaces this at the CLI boundary as ENVIRONMENT, and
            # swallowing it here made `update` the one command that reported
            # success after losing the race.
            #
            # This used to name `ConcurrentEditError`, which this path has never
            # been able to raise: the write goes through `update_bib_entry` and
            # the dry-run preview through `preview_write_plan`, and only the
            # latter refuses — as a `StalePlanError`. So the arm below was
            # swallowing the very failure this one exists to re-raise.
            raise
        except Exception as exc:  # one bad record must not abort the run
            failed_item: UpdatePlanItem = {
                "citekey": citekey,
                "changed_fields": [],
                "applied": False,
                "note": f"update failed: {exc}",
                "failed": True,
            }
            item = failed_item
        if isinstance(item, _PendingWrite):
            items.append(item.item)
            queued.append(item)
            chunk.append(item)
            if len(chunk) >= _WRITE_BATCH:
                flush()
        elif item is not None:
            items.append(item)
    flush()
    dropped = {id(p.item) for p in queued if not p.keep}
    items = [item for item in items if id(item) not in dropped]

    # A run where *every* item failed is not `ok`. A partial failure stays
    # `ok` and is reported through `errors` and each item's `failed`, which
    # is what the CLI runner turns into PARTIAL — promoting that to `error`
    # here would make one failed lookup exit 5.
    all_failed = bool(items and all(item.get("failed") for item in items))
    result: UpdateBibResult = {
        "status": "error" if all_failed else "ok",
        "bib_name": bib["name"],
        "dry_run": dry_run,
        "items": items,
        # Derived from the item outcomes, not hardcoded: `POST /update` returns
        # this verbatim, so a run in which every item failed answered 200
        # `{"status":"ok","errors":[]}`. The CLI runner computed its own verdict
        # from `failed`, so only the HTTP route was wrong — one list, read the
        # same way by both.
        "errors": [
            f"{item['citekey']}: {item.get('note') or 'update failed'}"
            for item in items
            if item.get("failed")
        ],
    }
    if all_failed:
        # `unavailable`, not `config`: the library was read and the entries were
        # there — the lookups or writes were not. An `error` without a `reason`
        # left `http_status` no classification to make, so `POST /update`
        # answered its documented fallback of 400. `promote_bib` classifies the
        # same run the same way.
        result["reason"] = REASON_UNAVAILABLE
    return result


def _plan_update_for_record(
    record: NormalizedRecord,
    citekey: str,
    *,
    bib_path: str,
    server_url: str,
    search_fn: SearchTranslationFetcher,
    records: list[NormalizedRecord],
    entries: list[BibtexEntry],
    dry_run: bool,
    metadata_confidence_min_score: int,
    file_path_style: str = DEFAULT_PDF_FILE_PATH_STYLE,
) -> UpdatePlanItem | _PendingWrite | None:
    """Plan one record's metadata enrichment; in a real run, queue its write.

    Returns the per-item result, a `_PendingWrite` for the caller to write in
    its next chunk (a real run only, so the lookups never hold the bib lock),
    or ``None`` when there is nothing to do (no
    query, no results, or no changed fields).  A network lookup failure is
    reported as a per-item note rather than raised; any *other* error is left to
    propagate so the caller's per-record guard can record it without aborting
    the whole update pass.
    """
    query = record.get("doi") or record.get("arxiv_id") or record.get("title")
    if not isinstance(query, str) or not query.strip():
        return None

    try:
        results = search_fn(query, server_url=server_url)
    except (OSError, ValueError) as exc:
        return {
            "citekey": citekey,
            "changed_fields": [],
            "applied": False,
            "note": f"lookup failed: {exc}",
            "failed": True,
        }

    if not results:
        return None

    selected = select_best_metadata_result(
        cast(list[Mapping[str, Any]], results),
        cast(Mapping[str, object], record),
    )
    metadata_diagnostics = metadata_result_diagnostics(
        cast(list[Mapping[str, Any]], results),
        cast(Mapping[str, object], record),
    )
    metadata_warnings = metadata_result_confidence_warnings(
        cast(list[Mapping[str, Any]], results),
        cast(Mapping[str, object], record),
        min_score=metadata_confidence_min_score,
    )
    candidate = selected["record"]
    rejection = _candidate_rejection(
        record, selected, min_score=metadata_confidence_min_score
    )
    if rejection is not None:
        return {
            "citekey": citekey,
            "changed_fields": [],
            "applied": False,
            "note": rejection,
            "skipped": True,
        }
    changed_fields = _changed_fields_for_candidate(record, candidate)
    if not changed_fields:
        return None

    note: str | None = None
    diff: str | None = None
    enriched = _conservative_enrich(
        cast(NormalizedRecord, dict(record)),
        cast(NormalizedRecord, dict(candidate)),
    )
    if dry_run:
        # Target the entry the real run will target. `update_bib_entry` above
        # resolves by *citekey*; `plan_bib_write` resolves by *identity*. Those
        # disagree precisely when enrichment supplies an identifier the library
        # does not carry yet — the ordinary case for this command — and the
        # preview then reported an insert for an entry the real run edits in
        # place.
        position = next(
            (i for i, on_disk in enumerate(records) if on_disk.get("citekey") == citekey),
            None,
        )
        if position is None:  # pragma: no cover — the caller sourced citekey from records
            note = _ENTRY_DISAPPEARED
        else:
            # Mirror `_apply_update`: project onto the entry on disk, so the
            # preview shows the same merge the write would perform rather than
            # a bare projection that drops unmodelled fields.
            updated_entry = apply_record_to_entry(entries[position], enriched)
            plan: WritePlan = {
                "action": "update",
                "index": position,
                "record": bibtex_entry_to_record(updated_entry),
                "entry": updated_entry,
                "changed_fields": changed_fields,
            }
            diff = preview_write_plan(
                bib_path, plan, file_path_style=file_path_style
            )["diff"]

    item: UpdatePlanItem = {
        "citekey": citekey,
        "changed_fields": changed_fields,
        "applied": False,
        "note": note,
    }
    if note == _ENTRY_DISAPPEARED:
        # The entry the run was told to update is no longer there, so this
        # record did not get what was asked for — in a dry run, the preview
        # could not be produced either.
        item["failed"] = True
    if diff is not None:
        item["diff"] = diff
    if metadata_diagnostics:
        item["metadata_diagnostics"] = metadata_diagnostics
    if metadata_warnings:
        item["metadata_warnings"] = metadata_warnings
    if not dry_run:
        return _PendingWrite(
            item=item, citekey=citekey, candidate=candidate, snapshot=record
        )
    return item


def _write_pending(
    bib_path: str, chunk: list[_PendingWrite], *, file_path_style: str
) -> None:
    """Write a chunk of looked-up enrichments in one session and report each.

    One lock, one parse and one atomic write for the chunk. Each entry is
    re-enriched against the record as it is on disk *now*, since the snapshot
    the run opened with may be stale, and is gated on its own round trip, so one
    unrepresentable entry fails its own item rather than the chunk.
    """
    try:
        with batch_write_session(bib_path, file_path_style=file_path_style) as session:
            for pending in chunk:
                _fold_into_session(session, pending)
    except StalePlanError:
        raise
    except Exception as exc:  # the session writes nothing when it raises
        for pending in chunk:
            pending.keep = True
            pending.item["applied"] = False
            pending.item["changed_fields"] = []
            pending.item["note"] = f"update failed: {exc}"
            pending.item["failed"] = True


def _fold_into_session(session: BatchWriteSession, pending: _PendingWrite) -> None:
    item = pending.item
    index = find_entry_index(session.entries, pending.citekey)
    if index is None:
        item["note"] = _ENTRY_DISAPPEARED
        item["failed"] = True
        return
    current_entry = session.entries[index]
    current_record = session.records[index]
    if not same_paper(pending.snapshot, current_record):
        # Per item, not a raise: the session writes nothing when it raises, and
        # the rest of the chunk is unaffected by one replaced entry.
        item["changed_fields"] = []
        item["note"] = (
            f"{pending.citekey} is now a different paper than the one looked up; "
            "left unchanged"
        )
        item["failed"] = True
        return
    current_enriched = _conservative_enrich(
        cast(NormalizedRecord, dict(current_record)),
        cast(NormalizedRecord, dict(pending.candidate)),
    )
    if not _changed_fields(current_record, current_enriched):
        # Another writer already filled what the candidate would add. Nothing
        # is committed, so nothing is reported: diffing the entry against its
        # own record instead listed `local_pdf_path` under a relative path
        # style, and the item read as applied.
        pending.keep = False
        return
    updated_entry = apply_record_to_entry(current_entry, current_enriched)
    updated_record = bibtex_entry_to_record(updated_entry)
    changed = _changed_fields(current_record, updated_record)
    if not changed:
        pending.keep = False
        return
    if updated_entry != current_entry:
        try:
            validate_bibtex_roundtrip([updated_entry])
        except Exception as exc:  # this entry only; the rest of the chunk proceeds
            item["changed_fields"] = []
            item["note"] = f"update failed: {exc}"
            item["failed"] = True
            return
        session.apply_plan(
            {
                "action": "update",
                "index": index,
                "record": updated_record,
                "entry": updated_entry,
                "changed_fields": changed,
            }
        )
    item["changed_fields"] = changed
    item["applied"] = True


def _needs_update(record: Mapping[str, object]) -> bool:
    venue = record.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return True
    if record.get("arxiv_id") and not record.get("doi"):
        return True
    if not record.get("year"):
        return True
    return False


def _conservative_enrich(
    existing: NormalizedRecord, incoming: NormalizedRecord
) -> NormalizedRecord:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in _USER_OWNED_UPDATE_FIELDS:
            continue
        current = merged.get(key)
        if current in (None, "", [], {}):
            merged[key] = value
    return cast(NormalizedRecord, merged)




#: Re-exported under the old private name so this module's call sites read the
#: same as before; the definition now lives in `identifiers` because `promote`
#: needs the identical test (promotion needs the identical test).
_has_preprint_identity = has_preprint_identity


def _candidate_rejection(
    record: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    min_score: int,
) -> str | None:
    """Why this candidate must not be written into the entry, or None.

    `update` had no acceptance gate at all: `select_best_metadata_result`
    returns the best of whatever came back, and a candidate scoring **−33** —
    a different title, one author of three, a different DOI — had its `venue`,
    `year` and `pdf_url` written in. `promote` has a hard threshold; this is the
    same idea, using the key the config already documents.

    Two rules, in order of certainty:

    - **Contradicting DOIs.** Both sides carry a DOI and they differ: these are
      not the same work, whatever the text similarity says. Preprint pairs are
      the deliberate exception (an arXiv DOI legitimately differs from the
      published one), and `pzi update --promote` is the command for those.
    - **Score below `metadata_confidence_min_score`.** Previously warn-only.
    """
    candidate = selected.get("record") if isinstance(selected, Mapping) else None
    if isinstance(candidate, Mapping):
        record_doi = canonical_doi(record.get("doi"))
        candidate_doi = canonical_doi(candidate.get("doi"))
        # The preprint exemption is deliberately narrow. `is_preprint` treats a
        # venue-less record as a preprint, which is most of what `update` runs
        # on — using it here would exempt nearly everything. Only a record with
        # an actual preprint identity is exempt, because that is the pairing
        # (arXiv DOI vs published DOI) that legitimately disagrees, and
        # `update --promote` is the command for it.
        if (
            record_doi
            and candidate_doi
            and record_doi != candidate_doi
            and not _has_preprint_identity(record)
        ):
            return (
                f"skipped: candidate DOI {candidate_doi} contradicts the entry's "
                f"{record_doi}"
            )
    # Does the candidate confirm *this* entry? `score_metadata_candidate` below
    # measures how rich a candidate is, never whether it is the same paper, and
    # `metadata_confidence_min_score` defaults to 0 — so a title search with no
    # DOI to contradict adopted whatever the provider returned. The review
    # reproduced "Attention Is All You Need" taking a beekeeping journal's DOI
    # and venue, applied, exit 0.
    #
    # Gated on the flag rather than a score cutoff, matching `capture_local_pdf`,
    # which reports the same situation as "title search returned a different
    # paper". `check_service` and `promote_service` score against the entry too;
    # `update` was the only metadata-writing path that did not.
    # Only when the candidate actually carries a title. `score_match` raises
    # `title_mismatch` for an absent one too, and a provider that returned a
    # venue and a year without a title has told us nothing about whether this is
    # the same paper — filling those in is what `update` is for.
    candidate_title = candidate.get("title") if isinstance(candidate, Mapping) else None
    record_title = record.get("title")
    same_title = (
        isinstance(candidate_title, str)
        and isinstance(record_title, str)
        and normalize_title(candidate_title) == normalize_title(record_title)
    )
    # `score_match` raises `title_mismatch` for titles too short to judge, even
    # when they are identical — `"T"` against `"T"` scores 0. Identical titles
    # cannot be a different paper, so they are never grounds to refuse.
    if isinstance(candidate_title, str) and candidate_title.strip() and not same_title:
        match = score_match(record, cast(Mapping[str, object], candidate))
        if "title_mismatch" in match["flags"]:
            return (
                f"skipped: candidate {candidate_title!r} is a different "
                f"paper (match {match['score']}/100)"
            )
    score = score_metadata_candidate(selected, cast(Mapping[str, object], record))
    if score < min_score:
        return (
            f"skipped: best candidate scored {score}, below "
            f"metadata_confidence_min_score={min_score}"
        )
    return None


def _changed_fields_for_candidate(
    existing: Mapping[str, object], candidate: Mapping[str, object]
) -> list[str]:
    enriched = _conservative_enrich(
        cast(NormalizedRecord, dict(existing)),
        cast(NormalizedRecord, dict(candidate)),
    )
    return _changed_fields(existing, enriched)


#: `update` only ever adds fields, so union-vs-`updated`-only gave the same
#: answer here — but `promote` removes fields, and having two spellings of
#: "what changed" is what let promotion under-report its own edits.
_changed_fields = changed_fields
