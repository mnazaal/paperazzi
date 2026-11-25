"""Remember that a per-entry lookup was already answered, so the next sweep can skip it.

Two commands are periodic audits over the whole library, and both are unusable
without a memory between runs:

* `update --promote` — most of a working library is preprints and most are not
  published yet, so the common outcome is "asked every provider, found
  nothing".  At ~13k candidates and seconds apiece, remembering that answer is
  the difference between a periodic audit and a thing you run once.
* `library check` — the politeness gate floors it at 0.6 s per entry, so a 22k
  library is ~3.7 hours.  Without a ledger every run re-audits every entry that
  was already fine, which is why `--limit` (audit the first N) was the only way
  to run it at all.

**Only the answer that means "stop asking" is recorded**, and each command's
version of that is the same shape:

* promote records a *negative* — nothing found.  A promoted entry stops being a
  candidate on its own (promote strips `arxiv_id`, so `has_preprint_identity`
  goes False) and `--mark-resolved` covers the keep-both case, so a positive
  marker would be redundant with two mechanisms that already exist.
* check records a *verified* verdict.  `problematic` is the thing the user has
  not fixed yet and must be re-asked every run; recording it would turn the
  ledger into a way to stop being told about a real defect.

Provider *failures* are recorded by neither: an outage is not an answer, and
freezing one into a month of silence would hide exactly the entries the sweep
exists to find.  That is why `could_not_verify` is not stored.

The ledger is a sidecar under `pzi_data_home`, never the `.bib`, so both
commands stay read-only against the library while still learning from what they
looked up — and so a check run cannot churn 22k entries of git history.

Each caller owns a filename (see `PROMOTE_FILENAME`, `CHECK_FILENAME`) and the
state within a file is keyed by bib name, so two libraries and two commands
never read each other's answers.

State is a plain dict and every function that reasons about it is pure; only
`load` and `save` touch disk.  Unreadable or malformed state is treated as
empty, never an error — a lost ledger costs one redundant sweep, whereas
refusing to run because a cache file is corrupt is worse than the problem it
was there to solve.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pzi.fileio import fsync_parent_dir
from pzi.similarity import extract_identities, title_identity

#: Sidecar filenames under ``pzi_data_home``, one per calling command. Separate
#: files rather than one keyed by command: the two horizons are set
#: independently, and pruning one must not walk the other's entries.
PROMOTE_FILENAME = "promote-checked.json"
CHECK_FILENAME = "check-verified.json"

#: Schema version.  A file whose version this code does not recognise is
#: treated as empty rather than migrated: the content is a cache of answers
#: already given, so discarding it costs one sweep and needs no migration code.
LEDGER_VERSION = 1


def ledger_path(data_home: str | Path, filename: str) -> Path:
    """Where a ledger lives for a given ``pzi_data_home``.

    *filename* is explicit at every call site rather than defaulted: a default
    would make "which ledger" invisible at the one place the two commands could
    silently end up sharing a file.
    """
    return Path(str(data_home)) / filename


def utc_now() -> datetime:
    """Current time, timezone-aware.  Injectable at the call sites for tests."""
    return datetime.now(UTC)


def entry_identity(record: Any) -> str:
    """A short digest of *which paper* a record is, for binding an answer to it.

    A ledger answer ("verified", "not published yet") is about a paper, not a
    citekey: another writer can delete an entry and add a different paper under
    the same key, and the old answer must not follow the key.  The identity is
    the DOI and arXiv id (the same two identifiers `bib_repository`'s
    `_require_same_paper` trusts, canonicalised by `extract_identities`) plus
    the normalised title.  The title is added because a DOI-less or
    arXiv-less replacement would otherwise look unchanged.

    Everything else is deliberately left out so ordinary enrichment (venue,
    pages, abstract, tags, an author-name reformat) does not invalidate the
    answer and force the sweep to redo work: a changed DOI, arXiv id or title
    is the only thing that re-asks.  Gaining a DOI re-asks once.
    """
    ids = sorted(
        f"{i['kind']}:{i['value']}"
        for i in extract_identities(record)
        if i["kind"] in ("doi", "arxiv")
    )
    title = title_identity(record.get("title") if isinstance(record, dict) else None)
    payload = json.dumps([ids, title], ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _format_ts(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _parse_ts(value: object) -> datetime | None:
    """Parse a stored timestamp, or ``None`` if it is unusable.

    The ledger is meant to be hand-inspectable and hand-editable, so a
    timestamp that someone mangled must degrade to "not checked" — the entry is
    simply looked up again.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A naive timestamp (hand-edited, or written by another tool) is read as
    # UTC rather than rejected: the alternative silently re-checks the entry
    # forever, and UTC is what this module writes.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _all_bibs(state: Any) -> dict[str, dict[str, Any]]:
    """Every per-bib map, or nothing at all for a shape this code did not write.

    One gate for every read: a state that is not a dict, carries a version this
    code does not know, or holds a `bibs` of the wrong type reads as empty here
    rather than at four separate call sites.
    """
    if not isinstance(state, dict) or state.get("version") != LEDGER_VERSION:
        return {}
    bibs = state.get("bibs")
    if not isinstance(bibs, dict):
        return {}
    return {
        name: entries
        for name, entries in bibs.items()
        if isinstance(name, str) and isinstance(entries, dict)
    }


def _entries(state: Any, bib_name: str) -> dict[str, Any]:
    """The per-bib map, or an empty one when this bib has no history."""
    return _all_bibs(state).get(bib_name, {})


def is_enabled(horizon_days: int) -> bool:
    """A horizon of zero (or less) disables the ledger completely.

    Off means off at both ends — nothing is consulted and nothing is written —
    so turning the setting off makes promote behave exactly as it did before
    this file existed, rather than leaving a sidecar quietly accruing.
    """
    return horizon_days > 0


def _stamp(value: object) -> object:
    """The timestamp of a stored entry, in either on-disk shape.

    Version 1 stored ``citekey -> timestamp``; entries now store
    ``citekey -> {"at": timestamp, "id": identity}``.  Both stay readable (and
    prunable by age) under the same ``version``; only the older shape lacks an
    identity, so it can never prove an answer is about *this* entry.
    """
    return value.get("at") if isinstance(value, dict) else value


def _is_fresh(value: object, *, now: datetime, horizon_days: int) -> bool:
    """True when a stored timestamp is inside the horizon."""
    checked_at = _parse_ts(_stamp(value))
    if checked_at is None:
        return False
    age_days = (now - checked_at).total_seconds() / 86400.0
    # A timestamp in the future (clock skew, or a hand-edit) counts as fresh.
    # The horizon is a floor on how often to re-ask, and re-asking early is the
    # thing it exists to prevent.
    return age_days < horizon_days


def is_recently_checked(
    state: Any,
    bib_name: str,
    citekey: str,
    *,
    now: datetime,
    horizon_days: int,
    identity: str,
) -> bool:
    """True when *citekey* was checked inside the horizon and should be skipped.

    *identity* (see :func:`entry_identity`) must equal the one recorded with the
    answer.  A pre-identity timestamp-only entry never matches: it cannot say
    which paper it was about, so it is re-asked once and rewritten with an
    identity, which costs one sweep like any other lost ledger.
    """
    if not is_enabled(horizon_days):
        return False
    value = _entries(state, bib_name).get(citekey)
    if not isinstance(value, dict) or value.get("id") != identity:
        return False
    return _is_fresh(value, now=now, horizon_days=horizon_days)


def record_checked(
    state: Any,
    bib_name: str,
    citekey: str,
    *,
    now: datetime,
    identity: str = "",
) -> dict[str, Any]:
    """Return a new state with *citekey* marked as checked at *now*.

    *identity* binds the answer to the entry that was looked up; the empty
    default matches no real :func:`entry_identity`, so omitting it records an
    answer that is never trusted.

    Pure: the input is never mutated, so a caller can accumulate a run's
    negatives and decide separately whether to persist them.
    """
    bibs = {name: dict(entries) for name, entries in _all_bibs(state).items()}
    bibs.setdefault(bib_name, {})[citekey] = {"at": _format_ts(now), "id": identity}
    return {"version": LEDGER_VERSION, "bibs": bibs}


def prune(state: Any, *, now: datetime, horizon_days: int) -> dict[str, Any]:
    """Drop entries past the horizon, and bibs left empty by that.

    An expired entry would be re-checked on the next sweep anyway, so keeping
    it is pure growth.  This is what bounds the file: one line per preprint that
    answered "not published", and never more than the library has preprints.
    """
    if not is_enabled(horizon_days):
        return state if isinstance(state, dict) else {}

    bibs: dict[str, Any] = {}
    for name, entries in _all_bibs(state).items():
        kept = {
            citekey: value
            for citekey, value in entries.items()
            if _is_fresh(value, now=now, horizon_days=horizon_days)
        }
        if kept:
            bibs[name] = kept
    return {"version": LEDGER_VERSION, "bibs": bibs}


def load(path: str | Path) -> dict[str, Any]:
    """Read the ledger.  Missing, unreadable or malformed all read as empty."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    if not isinstance(parsed, dict) or parsed.get("version") != LEDGER_VERSION:
        return {}
    return parsed


#: Answers a sweep holds before saving them. A save re-reads, merges and
#: rewrites the whole file — about 0.3 s for a 22k-entry ledger — while `check`
#: is floored at 0.6 s per entry, so saving every answer would cost a third of
#: the run. 25 matches promote's write chunk: a killed run loses at most 24
#: answers, and an interrupted one (Ctrl-C) loses none, because the caller
#: saves the rest on the way out.
SAVE_EVERY = 25


class PendingAnswers:
    """The answers one sweep has earned, saved while the sweep runs.

    A sweep ran for hours and saved only after its loop, so Ctrl-C on the last
    entry discarded every answer the run had earned. `add` saves every
    `SAVE_EVERY` answers, and the caller calls `save` from a ``finally:`` so an
    interrupt keeps the rest.

    Every save re-reads the file and merges this run's answers onto it, so a
    concurrent run sharing the file keeps the answers it saved in between.
    A disabled horizon makes this a no-op at both ends.
    """

    def __init__(
        self, path: str | Path, bib_name: str, *, now: datetime, horizon_days: int
    ) -> None:
        self._path = path
        self._bib_name = bib_name
        self._now = now
        self._horizon_days = horizon_days
        self._pending: list[tuple[str, str]] = []

    def add(self, citekey: str, identity: str) -> None:
        """Hold one answer, saving the batch once `SAVE_EVERY` are held."""
        if not is_enabled(self._horizon_days):
            return
        self._pending.append((citekey, identity))
        if len(self._pending) >= SAVE_EVERY:
            self.save()

    def save(self, *, even_if_empty: bool = False) -> None:
        """Merge the held answers onto a fresh read and write the result.

        *even_if_empty* still rewrites the file when nothing is held, which
        prunes expired entries.
        """
        if not is_enabled(self._horizon_days):
            return
        if not self._pending and not even_if_empty:
            return
        state: Any = load(self._path)
        for citekey, identity in self._pending:
            state = record_checked(
                state, self._bib_name, citekey, now=self._now, identity=identity
            )
        save(self._path, prune(state, now=self._now, horizon_days=self._horizon_days))
        self._pending.clear()


def save(path: str | Path, state: dict[str, Any]) -> None:
    """Write the ledger atomically.  Best-effort: write failures are swallowed.

    Losing a write costs one redundant sweep, so a full disk must not turn a
    successful promote run into a failed one.
    """
    target = Path(path)
    tmp: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=str(target.parent), suffix=".tmp", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            tmp = handle.name
        os.replace(tmp, str(target))
        fsync_parent_dir(target)
    except (OSError, TypeError, ValueError):
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
