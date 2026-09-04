"""The public Python API — what `import pzi` promises.

Everything here is frozen at 1.0 (decision 12): parameter names, defaults and
return shapes are a compatibility promise, and `tests/test_public_api.py` fails
if any of them move. Everything *not* here is internal, whatever its docstring
says, and may change in any release.

This is a facade, not a set of re-exports, and the difference is the point. The
service functions behind it are shaped for the CLI and for testing:
`add_input_to_bib` takes `config_path`, `home_dir`, `bib_selector` and twelve
injected fetcher parameters that exist so tests can replace the network.
Exporting that as a public API would freeze a dozen internals and hand a caller
arguments they have no way to supply. So the facade takes what a script
actually has — optionally a config path, optionally which library — resolves the
rest, and returns the data rather than the CLI's envelope.

Two conventions hold across every function:

- **Failure raises.** Services return ``{"status": "error", ...}`` because the
  CLI and the HTTP API both render it; a Python caller who forgets to check that
  gets silence, which is the wrong default for a library. Failures here raise
  :class:`pzi.errors.PziError`, carrying the same message and exit code the CLI
  would have used.
- **The return value is the answer, not the envelope.** ``search`` returns the
  matches; ``export`` returns the text. The envelope belongs to the transports.
"""

from __future__ import annotations

import functools
import os
import warnings
from collections.abc import Callable, Mapping
from typing import Any, NotRequired, TypedDict, cast

from pzi import exit_codes
from pzi.add_planning import AddResult
from pzi.bib_service import (
    SORT_FIELDS,
    BibInfo,
    DeleteEntryResult,
    EntryRecord,
    EntrySummary,
    clamp_limit,
    delete_entry,
    entry_detail,
    list_entries,
)
from pzi.bib_service import list_bibs as _list_bibs_service
from pzi.capture_core import capture_to_bib
from pzi.capture_models import CaptureInput, CaptureOptions
from pzi.check_service import CheckItem, CheckResult, check_bib
from pzi.clean_service import plan_pdf_disposal
from pzi.config import (
    AppConfig,
    BibConfig,
    BibResolutionFailure,
    default_config_path,
    load_bib_target,
)
from pzi.dedupe_service import (
    DedupeResult,
    MergeResult,
    find_duplicates,
    merge_duplicates,
)
from pzi.errors import (
    REASON_CONFIG,
    REASON_UNAVAILABLE,
    REASON_USAGE,
    PziError,
    exit_code_for_error,
)
from pzi.export_service import EXPORTERS
from pzi.promote_service import PromoteItem, PromoteResult, promote_bib
from pzi.search_service import SearchMatch, search_bib
from pzi.tag_service import TagChangeResult, TagListResult
from pzi.tag_service import add_tags as _add_tags_service
from pzi.tag_service import list_tags as _list_tags_service
from pzi.tag_service import remove_tags as _remove_tags_service
from pzi.update_service import UpdateBibResult, UpdatePlanItem, update_bib

__all__ = [
    "AddReport",
    "BibInfo",
    "CheckItem",
    "CheckReport",
    "DedupeReport",
    "DeleteEntryReport",
    "EntryPage",
    "EntryRecord",
    "EntrySummary",
    "MergeReport",
    "PromoteItem",
    "PromoteReport",
    "SearchMatch",
    "TagChangeReport",
    "TagListReport",
    "UpdateBibReport",
    "UpdatePlanItem",
    "add",
    "add_tags",
    "check",
    "dedupe",
    "delete",
    "entries",
    "export",
    "get",
    "list_bibs",
    "list_tags",
    "merge",
    "promote",
    "remove_tags",
    "search",
    "update",
]

#: What the facade strips before returning. `status` is always ``"ok"`` — a
#: failure raises `PziError` — and no service sets `reason` on a success
#: (verified across every result literal in `src/pzi/`; the reason a caller
#: wants is on the raised `PziError`, which carries it). Keys that can never
#: vary are keys frozen into the 1.0 surface saying nothing, against this
#: module's own stated convention that it returns the answer rather than the
#: transport envelope.
#:
#: `errors` is **not** always empty, which an earlier version of this comment
#: claimed: `check`, `update` and `promote` deliberately return ``status:
#: "ok"`` with non-empty ``errors`` on a *partial* failure — a provider
#: unreachable for some entries, a lookup failed for one citekey — and the CLI
#: exits 4 (PARTIAL) on the very same result. For those three sweeps the key is
#: part of the answer, so their reports keep it (`_REPORTS_KEEPING_ERRORS`);
#: for the other six a failure can only raise, so theirs is provably dead and
#: is stripped. Decision 40.
#:
#: The service types keep the whole envelope: the CLI and the HTTP API both
#: read `status` to choose an exit code or a status line. So each has a public
#: twin below, paired in `_REPORT_TYPES`, and `tests/test_public_api.py`
#: asserts the derivation — a service growing a key cannot silently skip its
#: public type.
_ENVELOPE_KEYS = frozenset({"status", "errors", "reason"})

#: The public report names whose `errors` is data rather than transport — the
#: three network sweeps where ok-with-errors is a real outcome. Read by
#: `_report` and by the derivation test, which demands this exact list.
_REPORTS_KEEPING_ERRORS = frozenset({"CheckReport", "PromoteReport", "UpdateBibReport"})


class AddReport(TypedDict):
    """One capture — what :func:`pzi.add` returns.

    `AddResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    bib_name: str | None
    bib_path: str | None
    action: str | None
    citekey: str | None
    pdf_path: str | None
    changed_fields: list[str]
    dry_run: bool
    message: str
    warnings: list[str]
    pdf_url: NotRequired[str | None]
    pdf_status: NotRequired[str | None]
    pdf_error: NotRequired[str | None]
    pdf_suggestion: NotRequired[str | None]
    metadata_diagnostics: NotRequired[list[str]]
    diff: NotRequired[str]


class CheckReport(TypedDict):
    """One reference audit — what :func:`pzi.check` returns.

    `CheckResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    #: Partial-failure channel: non-empty when the sweep succeeded for
    #: some entries and failed for others (the CLI exits 4 on the same
    #: result). Kept by decision 40; empty on a clean run.
    errors: list[str]
    bib_name: str | None
    strict: bool
    total: int
    counts: dict[str, int]
    items: list[CheckItem]
    warnings: list[str]
    #: Entries `recheck_after_days` skipped as already verified. `total` counts
    #: only what was audited, so without this a caller cannot tell a sweep with
    #: nothing left to do from a library that lost its entries.
    skipped_fresh: int


class DedupeReport(TypedDict):
    """The duplicate clusters :func:`pzi.dedupe` found.

    `DedupeResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    bib_path: str
    total_entries: int
    exact_duplicates: list[dict[str, Any]]
    fuzzy_candidates: list[dict[str, Any]]
    total_clusters: int
    warnings: NotRequired[list[str]]


class DeleteEntryReport(TypedDict):
    """One deletion — what :func:`pzi.delete` returns.

    `DeleteEntryResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    citekey: str
    bib_path: str
    message: str
    dry_run: NotRequired[bool]
    title: NotRequired[str]
    pdf_path: NotRequired[str | None]
    #: What became of ``pdf_path``. Absent under ``keep_pdf`` or when the
    #: entry had no PDF. See `pzi.clean_service.QuarantineResult`.
    pdf_action: NotRequired[dict[str, Any]]
    backup_path: NotRequired[str]


class MergeReport(TypedDict):
    """One merge — what :func:`pzi.merge` returns.

    `MergeResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    citekey_a: str
    citekey_b: str
    dry_run: bool
    message: str
    merged_title: NotRequired[str]
    #: What became of ``orphaned_pdf``. Absent under ``keep_pdf`` or when the
    #: merge kept the only PDF. See `pzi.clean_service.QuarantineResult`.
    pdf_action: NotRequired[dict[str, Any]]
    dropped_citekey: NotRequired[str]
    changed_fields: NotRequired[list[str]]
    merged_record: NotRequired[dict[str, Any]]
    carried_fields: NotRequired[list[str]]
    dropped_fields: NotRequired[list[str]]
    orphaned_pdf: NotRequired[str]
    overwritten_fields: NotRequired[list[str]]
    backup_path: NotRequired[str]


class PromoteReport(TypedDict):
    """One promotion sweep — what :func:`pzi.promote` returns.

    `PromoteResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    #: Partial-failure channel: non-empty when the sweep succeeded for
    #: some entries and failed for others (the CLI exits 4 on the same
    #: result). Kept by decision 40; empty on a clean run.
    errors: list[str]
    bib_name: str | None
    dry_run: bool
    keep_preprint: bool
    items: list[PromoteItem]
    summary: NotRequired[dict[str, Any]]


class TagChangeReport(TypedDict):
    """One tag write — what :func:`pzi.add_tags` and
    :func:`pzi.remove_tags` return.

    `TagChangeResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    bib_name: str | None
    citekey: str | None
    tags: list[str]
    changed: bool
    dry_run: bool
    message: str


class TagListReport(TypedDict):
    """A tag listing — what :func:`pzi.list_tags` returns.

    `TagListResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    bib_name: str | None
    citekey: str | None
    tags: list[str]
    warnings: NotRequired[list[str]]


class UpdateBibReport(TypedDict):
    """One metadata sweep — what :func:`pzi.update` returns.

    `UpdateBibResult` minus the transport envelope. See `_ENVELOPE_KEYS`.
    """

    #: Partial-failure channel: non-empty when the sweep succeeded for
    #: some entries and failed for others (the CLI exits 4 on the same
    #: result). Kept by decision 40; empty on a clean run.
    errors: list[str]
    bib_name: str | None
    dry_run: bool
    items: list[UpdatePlanItem]

class EntryPage(TypedDict):
    """One page of the library — what :func:`pzi.entries` returns.

    A page rather than a bare list because `total` is the answer to "am I done
    paginating", and there is no other way to get it: the service computes it
    and the facade used to throw it away, so a caller looping over `offset` had
    to keep requesting until a short page came back. `offset` and `limit` are
    echoed as *resolved* — `limit` is clamped to `MIN_LIMIT..MAX_LIMIT` and a
    negative `offset` becomes zero, matching the other two front ends, so what
    comes back says what was actually used rather than what was asked for.
    """

    items: list[EntrySummary]
    #: Entries in the library, not on this page. `len(items)` is the page.
    total: int
    offset: int
    limit: int


#: Public type <- service type. Read by the derivation test, and the single
#: place the pairing is written down.
_REPORT_TYPES: tuple[tuple[type, type], ...] = (
    (AddReport, AddResult),
    (CheckReport, CheckResult),
    (DedupeReport, DedupeResult),
    (DeleteEntryReport, DeleteEntryResult),
    (MergeReport, MergeResult),
    (PromoteReport, PromoteResult),
    (TagChangeReport, TagChangeResult),
    (TagListReport, TagListResult),
    (UpdateBibReport, UpdateBibResult),
)


def _report(result: Mapping[str, Any], *, keep_errors: bool = False) -> Any:
    """A service result with the transport envelope removed.

    Called after `_unwrap`, so the envelope has already been read for its one
    purpose: deciding whether to raise. ``keep_errors=True`` is the three-sweep
    exception (`_REPORTS_KEEPING_ERRORS`): there ``errors`` reports the failed
    part of a partially-successful run and must survive the strip.
    """
    stripped = _ENVELOPE_KEYS - {"errors"} if keep_errors else _ENVELOPE_KEYS
    report = {key: value for key, value in result.items() if key not in stripped}
    if keep_errors:
        report.setdefault("errors", [])
    return report


#: `bib_service` owns the export table, the sort fields and the page bounds;
#: all three front ends import them from there rather than restating them.
_SORT_FIELDS = tuple(sorted(SORT_FIELDS))


def _home() -> str:
    """The real home directory.

    Deliberately not a parameter. `home_dir` exists on every service function so
    tests can point config resolution at a temp directory; a library caller has
    no reason to override where their own home is, and exposing it would freeze
    a test seam as public API.
    """
    return os.path.expanduser("~")


def _resolved_config_path(config_path: str | None) -> str:
    """The config to read, following the CLI's documented precedence.

    ``config_path`` argument, then ``$PZI_CONFIG``, then the XDG default —
    `cli.py:166-172`. Honouring the environment variable here is what stops
    ``pzi search`` and ``pzi.search()`` reading different files for anyone who
    sets it.
    """
    if config_path is not None:
        return config_path
    return os.environ.get("PZI_CONFIG") or default_config_path(_home())


def _emit_warnings(result: Mapping[str, Any]) -> None:
    """Re-raise a service's read warnings through Python's own channel.

    Reading a *missing* bib is a warning rather than an error on purpose
    (`bib_repository.describe_missing_bib`): a freshly ``pzi init``-ed config
    names a bib that does not exist until the first ``add``. The CLI prints
    those warnings and the HTTP envelope carries them — but a facade returning
    only the items dropped them, so a typo'd ``path =`` or an unmounted share
    came back as an empty list, indistinguishable from a library with nothing
    in it.

    `warnings.warn` rather than a changed return type: it is the mechanism a
    Python caller already has, it is visible by default, and ``-W error``
    escalates it for a script that would rather stop.

    ``stacklevel=4`` counts past `_public`'s `wrapper`, not to it: here, the
    public function, the wrapper, the caller. At 3 it stopped on the wrapper,
    so every warning this module raises reported the same origin — one line
    inside `api.py`. Python's default filter shows a warning once per (message,
    category, module, lineno), so the second and third call in a process were
    dropped: three reads of a missing bib produced one warning, and it pointed
    at pzi's source rather than the line that asked.

    **The rule, applied by every read:** warnings are always emitted here, and a
    return that is report-shaped carries them as well. What is left asymmetric
    is the *return*, not the emission, and it follows from the return type:
    `search` hands back a bare `list` and `get` an `EntryRecord`, so there is
    nowhere to put a `warnings` key, while `DedupeReport`/`CheckReport`/
    `TagListReport` already have one.

    Before this rule there were three behaviours. `dedupe` and `check` returned
    warnings without emitting, so `-W error` stopped a script reading a missing
    bib through `list_tags` and let the identical condition through `dedupe`
    unnoticed.
    """
    for warning in result.get("warnings") or []:
        warnings.warn(str(warning), UserWarning, stacklevel=4)


def _public(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Make `except PziError` actually hold for a public function.

    `_unwrap` only translates the services' own ``status == "error"``. Anything
    raised *below* that — the whole I/O surface — escaped as a bare `OSError`,
    so a caller following this module's documented idiom crashed on an
    unreadable bib or a `path =` pointing at a directory, both of which the CLI
    reports as exit 5.

    A decorator rather than a `try` in each function: seven copies of the same
    three lines is how one of them ends up missing it. `functools.wraps` keeps
    `__wrapped__` set, so `inspect.signature` — and therefore the frozen public
    API snapshot — still sees the real signature.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except PziError:
            raise
        except OSError as exc:
            raise PziError(
                f"{type(exc).__name__}: {exc}",
                code=exit_codes.ENVIRONMENT,
                reason=REASON_UNAVAILABLE,
            ) from exc

    return wrapper


def _tag_list(tags: object) -> list[str]:
    """Validate a caller's tag argument, or raise what the CLI would report.

    `list(tags)` on its own accepted a bare string and iterated it: a
    `pzi.add_tags(key, "nlp")` wrote ``keywords = {l, n, p}`` into the library
    and reported ``status: ok, changed: True``. The annotation said `list[str]`
    and nothing enforced it — the CLI cannot reach this (it splits CSV into a
    list) and `POST /tags/add` already answers 400 for exactly this shape, so
    the Python API was the one surface that took the mistake and wrote it.

    Rejecting rather than coercing, because a bare string is ambiguous: `"nlp"`
    is one tag and `"a,b"` is arguably two. Accepting one later is a widening
    and stays backward-compatible; guessing now and changing the guess is not.

    A `list` exactly — not any iterable — matching both the annotation and
    `POST /tags/add`'s validation, so the two programmatic surfaces refuse the
    same shapes. A tuple is unambiguous and could be admitted later; that too
    is a widening.
    """
    if isinstance(tags, str) or not isinstance(tags, list):
        raise PziError(
            f"tags must be a list of strings, not {type(tags).__name__}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    if not all(isinstance(tag, str) for tag in tags):
        raise PziError(
            "tags must be a list of strings",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    return list(tags)


def _unwrap(result: Mapping[str, Any], key: str) -> Any:
    """Return ``result[key]``, or raise what the CLI would have reported.

    `exit_code_for_error` is the CLI's own mapping from a service's structured
    `reason` to an exit code, reused here so a caller catching `PziError` sees
    the same classification a shell script branching on `$?` would.

    The `reason` is passed through as well as folded into the code, because the
    code loses information the caller may need: `ENVIRONMENT` covers config,
    unavailable *and* conflict, so branching on it alone cannot separate a bad
    `path =` from a provider that is down. `exit_code_for_error` has the value
    in hand here; forwarding it costs nothing and is what `PziError.reason`
    exists for. The CLI's own fallback is `exc.reason or <coarse map>`, i.e.
    the map is for raisers that did not know. This one knows.
    """
    if result.get("status") == "error":
        errors = [str(error) for error in result.get("errors") or []]
        message = str(result.get("message") or "") or (
            errors[0] if errors else "the command failed"
        )
        reason = result.get("reason")
        raise PziError(
            message,
            code=exit_code_for_error(result),
            details=errors,
            reason=reason if isinstance(reason, str) else None,
        )
    return result[key]


def _bib_target(
    config_path: str | None, library: str | None
) -> tuple[AppConfig, BibConfig]:
    """Resolve which library to act on, for the services that take a path.

    `export`, `dedupe`, `delete` and `merge` take a `bib_path` rather than a
    selector, so the resolution the other services do internally has to happen
    here.

    Returns the *config* as well as the library, because a path is not always
    enough: `merge_duplicates` also needs `pdf_file_path_style`, and both other
    front ends read it from the config (`commands/dedupe.py`). A facade that
    resolved only the path would silently write absolute PDF paths into a
    library configured for relative ones.
    """
    resolved = load_bib_target(
        config_path=_resolved_config_path(config_path), home_dir=_home(), bib_selector=library
    )
    if isinstance(resolved, BibResolutionFailure):
        raise PziError(
            "; ".join(resolved.errors) or "could not resolve a library",
            details=list(resolved.errors),
            reason=REASON_CONFIG,
        )
    return resolved


def _bib_path(config_path: str | None, library: str | None) -> str:
    """The resolved library's path, for the services that need only that."""
    _config, bib = _bib_target(config_path, library)
    return bib["path"]


@_public
def search(
    query: str | None = None,
    *,
    author: str | None = None,
    year: int | None = None,
    tag: str | None = None,
    venue: str | None = None,
    doi: str | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    config_path: str | None = None,
    library: str | None = None,
) -> list[SearchMatch]:
    """Return entries matching the given filters, combined with AND.

    At least one filter is required. Matching is case-insensitive.

    *venue* matches ``journal``, ``booktitle`` or ``venue``; *doi* is a
    case-insensitive substring, so a bare suffix finds the entry.

    *sort*, *offset* and *limit* page the result the same way :func:`entries`
    does, through the same ordering. *limit* defaults to `None` — every match —
    because search has never capped its output and a default page would change
    what existing callers receive (decision 42).
    """
    # Checked here rather than left to the service, which words the same refusal
    # as "provide at least one of --query, --author, --year, --tag" — flag names
    # a caller of this function never typed. The unresolved-library messages had
    # the same problem and were made flag-neutral in
    # `config._unresolved_target_error`.
    #
    # Not the last of them, though this comment used to claim so:
    # `add_planning.similarity_hint_warnings` puts `pzi library merge`,
    # `pzi library dedupe` and `--force-new` into `AddResult["warnings"]`, which
    # `add()` returns verbatim. Making *that* flag-neutral is a bigger change —
    # the warning would have to become a structured discriminator, the way
    # `reason` already is, with each surface rendering its own wording — so it
    # is noted rather than done here.
    if all(f is None for f in (query, author, year, tag, venue, doi)):
        raise PziError(
            "search needs at least one of query, author, year, tag, venue or doi",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    if offset < 0:
        raise PziError(
            f"offset must be zero or greater, got {offset}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    if limit is not None and limit < 1:
        raise PziError(
            # Same reasoning as `check(limit=...)`: this front end has no parser
            # to reject it, and `limit=0` would silently return no matches from
            # a search that found some.
            f"limit must be at least 1, got {limit}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    result = search_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        query=query,
        author=author,
        year=year,
        tag=tag,
        venue=venue,
        doi=doi,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    typed = dict(result)
    matches = list(_unwrap(typed, "matches"))
    _emit_warnings(typed)
    return matches


@_public
def entries(
    *,
    offset: int = 0,
    limit: int = 50,
    sort: str = "citekey",
    config_path: str | None = None,
    library: str | None = None,
) -> EntryPage:
    """Return a page of the library, sorted by ``citekey``, ``title``,
    ``author`` or ``year``.

    The entries are in ``["items"]``; ``["total"]`` is the whole library, which
    is what tells a caller when to stop paginating::

        page = pzi.entries()
        while page["offset"] + len(page["items"]) < page["total"]:
            page = pzi.entries(offset=page["offset"] + len(page["items"]))
    """
    if sort not in _SORT_FIELDS:
        raise PziError(
            f"unknown sort field {sort!r} — expected one of "
            f"{', '.join(_SORT_FIELDS)}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    resolved_offset = max(0, offset)
    resolved_limit = clamp_limit(limit)
    result = list_entries(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        offset=resolved_offset,
        limit=resolved_limit,
        sort=sort,
    )
    typed = dict(result)
    items = list(_unwrap(typed, "items"))
    _emit_warnings(typed)
    return {
        "items": items,
        "total": int(typed.get("total", len(items))),
        "offset": resolved_offset,
        "limit": resolved_limit,
    }


@_public
def export(
    fmt: str = "bibtex",
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> str:
    """Return the whole library serialized as ``bibtex``, ``json``, ``csv`` or
    ``ris``."""
    exporter = EXPORTERS.get(fmt)
    if exporter is None:
        raise PziError(
            f"unknown export format {fmt!r} — expected one of "
            f"{', '.join(sorted(EXPORTERS))}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    return str(_unwrap(dict(exporter(_bib_path(config_path, library))), "content"))


@_public
def get(
    citekey: str,
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> EntryRecord:
    """Return one entry's full record by citekey.

    This is where a script finds a paper's PDF: ``local_pdf_path`` is the path
    recorded in the entry's ``file`` field, which the summaries from
    :func:`entries` and :func:`search` cannot carry — they report only
    ``has_pdf``.

    Raises :class:`pzi.errors.PziError` with ``code == 3`` if no entry has that
    citekey, following this module's convention that failure raises. A caller
    who wants a soft lookup can catch it.
    """
    result = entry_detail(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        citekey=citekey,
        bib_selector=library,
    )
    record = _unwrap(result, "record")
    _emit_warnings(result)
    # `entry_detail` is typed `dict[str, Any]`, so the cast is where the claim
    # "this is an `EntryRecord`" is actually made. What backs it is
    # `bibtex_entry_to_record`, which sets exactly these keys on every record it
    # parses, and the conformance test in `tests/test_public_api.py`, which
    # compares a real call's keys against the declaration.
    return cast("EntryRecord", dict(record))


@_public
def list_bibs(*, config_path: str | None = None) -> list[BibInfo]:
    """Return the configured libraries: name, path, papers dir, and default.

    Every other function takes ``library=`` by name, and without this there was
    no supported way to discover what those names are — the config file is not
    part of the API.

    Takes no ``library``: it is the function that tells you what the valid
    values are.
    """
    result = _list_bibs_service(
        config_path=_resolved_config_path(config_path), home_dir=_home()
    )
    typed = dict(result)
    return list(_unwrap(typed, "bibs"))


@_public
def add(
    source: str,
    *,
    tags: list[str] | None = None,
    dry_run: bool = False,
    force_new: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> AddReport:
    """Capture a paper by DOI, URL or local PDF path.

    Returns the write result: the citekey, whether it was an insert or an
    update, the PDF path if one was attached, and any warnings. Makes network
    requests.

    **Requires a running translation server** — start one with ``pzi server``.
    Unlike the CLI, this does not install or start one for you: downloading a
    Node runtime and leaving a process behind is a lot of hidden behaviour for
    one function call. Without it, capture falls back to the DOI-based metadata
    providers, which cannot resolve a publisher URL.
    """
    # Validate before the truthiness test, or a falsy wrong type slips the
    # guard: `tags=""` silently meant "no tags" while `tags="nlp"` raised.
    tag_overrides = _tag_list(tags) if tags is not None else []
    overrides: dict[str, object] = {"tags": tag_overrides} if tag_overrides else {}
    # Through `capture_to_bib`, the seam the CLI and the HTTP API both use —
    # not straight to `add_input_to_bib`. It carries the shared capture policy
    # (PDF-candidate ranking, the SSRF check on a page-supplied PDF URL), so a
    # policy added there applies to this front end too. Bypassing it meant the
    # newest surface silently had the oldest behaviour.
    result = capture_to_bib(
        CaptureInput(
            value=source,
            record_overrides=overrides,
            bib_selector=library,
        ),
        CaptureOptions(dry_run=dry_run, force_new=force_new),
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
    )
    # `capture_to_bib` is typed as a bare `Mapping`, so this is the cast that
    # claims the capture shape. Both of its builders — `add_planning
    # .error_result` and `capture_local_pdf.build_add_record_result` — are
    # annotated `AddResult`, and the conformance test checks a real call.
    typed = cast("AddResult", dict(result))
    _unwrap(typed, "status")
    return _report(typed)


@_public
def check(
    *,
    strict: bool = False,
    limit: int | None = None,
    recheck_after_days: int = 0,
    config_path: str | None = None,
    library: str | None = None,
) -> CheckReport:
    """Verify entries against metadata providers. Makes network requests.

    *limit* audits only the first N entries. The politeness gate floors this
    command at 0.6 s/entry best case, so a whole-library run is hours; without
    this the programmatic front end had no way to ask for a smaller one, while
    the CLI has had ``--limit`` since item 550.

    *recheck_after_days* skips entries a previous run verified inside that many
    days, so repeated calls work through a large library instead of re-auditing
    its healthy entries. Zero (the default) disables the ledger at both ends.
    Mirrors the CLI's ``--recheck-after``.

    Raises :class:`PziError` if *recheck_after_days* is negative. The CLI
    rejects it at the parser (`_non_negative_int`); this front end has none, and
    a negative horizon would make every stored timestamp read as stale — an
    argument that looks like it skips work but silently guarantees the most.

    Raises :class:`PziError` if *limit* is less than 1. The CLI's `--limit`
    rejects `0` and negatives at the parser (`cli_parser._positive_int`); this
    front end has no parser; without this check, `limit=0` reached
    `check_bib`, which audited nothing and returned `status: "ok"` — a clean
    bill of health for a run that checked zero entries — and a negative
    `limit` meant "unlimited" to `check_bib`, silently auditing the whole
    library from a flag that looked like it capped the run.
    """
    if limit is not None and limit < 1:
        raise PziError(
            f"limit must be at least 1, got {limit}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    if recheck_after_days < 0:
        raise PziError(
            f"recheck_after_days must be zero or greater, got {recheck_after_days}",
            code=exit_codes.USAGE,
            reason=REASON_USAGE,
        )
    result = check_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        strict=strict,
        limit=limit,
        recheck_after_days=recheck_after_days,
    )
    typed = result.copy()
    _unwrap(typed, "status")
    _emit_warnings(typed)
    return _report(typed, keep_errors=True)


@_public
def dedupe(
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> DedupeReport:
    """Report exact duplicate clusters and fuzzy near-duplicates. Reads only."""
    typed = find_duplicates(bib_path=_bib_path(config_path, library)).copy()
    _unwrap(typed, "status")
    _emit_warnings(typed)
    return _report(typed)


@_public
def promote(
    *,
    keep_preprint: bool = False,
    dry_run: bool = True,
    config_path: str | None = None,
    library: str | None = None,
) -> PromoteReport:
    """Find published versions of preprints. Makes network requests.

    **Previews by default** — pass ``dry_run=False`` to write. This sweeps the
    whole library and queries a provider per preprint, so a zero-argument call
    that wrote would rewrite thousands of entries over the network before the
    caller saw anything. `promote_bib` and ``POST /promote`` both preview by
    default for the same reason; the CLI writes because you typed the command.

    By default the preprint is **replaced in place**, keeping its citekey so
    existing citations still resolve; ``keep_preprint=True`` creates the
    published entry beside it instead.

    The parameter is named for what it does rather than inverted into
    ``replace``: the service, the CLI flag and the HTTP body key are all
    ``keep_preprint`` now, so there is one name and no double negative to read
    through.
    """
    result = promote_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        dry_run=dry_run,
        keep_preprint=keep_preprint,
    )
    typed = result.copy()
    _unwrap(typed, "status")
    return _report(typed, keep_errors=True)


@_public
def delete(
    citekey: str,
    *,
    dry_run: bool = True,
    keep_pdf: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> DeleteEntryReport:
    """Delete one entry by citekey. **Previews by default.**

    Pass ``dry_run=False`` to actually delete. A timestamped copy of the
    pre-delete library is made first, and its path is in the returned
    ``backup_path``. The entry's PDF is moved to ``papers_dir/.orphans/`` and
    the move reported as ``pdf_action``; pass ``keep_pdf=True`` to leave it
    where it is. It is never unlinked, so ``backup_path`` and the quarantined
    file together undo the call.

    Previewing is the odd one out among the writers here, and deliberately so.
    The general rule is that a function naming exactly what it touches acts —
    :func:`add_tags` does, :func:`merge` previews because it sweeps. But
    ``delete`` is the one call in this module that destroys data, and the other
    two surfaces both hesitate: the CLI refuses without ``--force`` and
    ``POST /delete`` previews. A Python API that deleted on the first call was
    the only surface where a typo'd citekey in a REPL took the entry out.
    """
    config, target = _bib_target(config_path, library)
    typed = delete_entry(
        bib_path=target["path"],
        citekey=citekey,
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    pdf_action = plan_pdf_disposal(
        result=typed, config=config, target=target,
        keep_pdf=keep_pdf, dry_run=dry_run,
    )
    if pdf_action is not None:
        typed["pdf_action"] = pdf_action
    return _report(typed)


@_public
def add_tags(
    citekey: str,
    tags: list[str],
    *,
    dry_run: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> TagChangeReport:
    """Add tags to one entry, keeping the tags it already has. Writes.

    Tags are normalized the way every other front end normalizes them, and the
    result's ``tags`` is the entry's full tag list afterwards, not the argument.
    ``changed`` says whether the entry was actually rewritten — adding a tag it
    already carries is a no-op, not an error.
    """
    typed = _add_tags_service(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        citekey=citekey,
        tags=_tag_list(tags),
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    return _report(typed)


@_public
def remove_tags(
    citekey: str,
    tags: list[str],
    *,
    dry_run: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> TagChangeReport:
    """Remove tags from one entry. Writes.

    The counterpart of :func:`add_tags`, with the same result shape. Removing a
    tag the entry does not have leaves ``changed`` false.
    """
    typed = _remove_tags_service(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        citekey=citekey,
        tags=_tag_list(tags),
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    return _report(typed)


@_public
def merge(
    citekey_a: str,
    citekey_b: str,
    *,
    dry_run: bool = True,
    keep_pdf: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> MergeReport:
    """Merge entry *citekey_a* into *citekey_b*, keeping b's citekey.

    This is what makes :func:`dedupe` actionable: it reports duplicate clusters,
    and this acts on a pair.

    **Previews by default** — pass ``dry_run=False`` to write. A merge drops one
    of the two entries, and the preview names exactly what happens: the fields
    the survivor takes over (``carried_fields``), the ones it loses to the
    dropped entry (``overwritten_fields``), and the PDF left orphaned if the
    survivor keeps its own (``orphaned_pdf``). The CLI writes because you typed
    the command; the same split as :func:`promote`.
    """
    config, bib = _bib_target(config_path, library)
    typed = merge_duplicates(
        bib_path=bib["path"],
        citekey_a=citekey_a,
        citekey_b=citekey_b,
        dry_run=dry_run,
        # Read from the config, as both other front ends do. Defaulting to
        # `absolute` here would rewrite a relative-path library's `file =`
        # fields to absolute ones on an unrelated merge.
        file_path_style=config.get("pdf_file_path_style", "absolute"),
    ).copy()
    _unwrap(typed, "status")
    pdf_action = plan_pdf_disposal(
        result=typed, config=config, target=bib, keep_pdf=keep_pdf, dry_run=dry_run,
    )
    if pdf_action is not None:
        typed["pdf_action"] = pdf_action
    return _report(typed)


@_public
def list_tags(
    citekey: str | None = None,
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> TagListReport:
    """List tags — one entry's, or the whole library's vocabulary.

    With *citekey*, the tags on that entry. Without one, every distinct tag in
    the library, which is what makes `search(tag=...)` usable: a caller can
    discover the vocabulary instead of guessing at it.
    """
    typed = _list_tags_service(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        citekey=citekey,
    ).copy()
    tags = _unwrap(typed, "tags")
    _emit_warnings(typed)
    typed["tags"] = list(tags)
    return _report(typed)


@_public
def update(
    *,
    dry_run: bool = True,
    config_path: str | None = None,
    library: str | None = None,
) -> UpdateBibReport:
    """Fill missing metadata on entries that have gaps. Makes network requests.

    **Previews by default** — pass ``dry_run=False`` to write, the same split as
    :func:`promote`. A zero-argument call sweeps the whole library and queries a
    provider per incomplete entry, so on a large library that is thousands of
    requests and a rewrite before the caller sees anything. `update_bib` and the
    CLI's `--dry-run` agree; the CLI writes by default because you typed the
    command.

    Conservative by construction: it fills gaps and never replaces a preprint
    with its published version. That is :func:`promote`.
    """
    typed = update_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    return _report(typed, keep_errors=True)
