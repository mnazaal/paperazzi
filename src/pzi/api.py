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
from typing import Any, cast

from pzi import exit_codes
from pzi.add_planning import AddResult
from pzi.bib_service import (
    BibInfo,
    DeleteEntryResult,
    EntryRecord,
    EntrySummary,
    delete_entry,
    entry_detail,
    list_entries,
)
from pzi.bib_service import list_bibs as _list_bibs_service
from pzi.capture_core import capture_to_bib
from pzi.capture_models import CaptureInput, CaptureOptions
from pzi.check_service import CheckResult, check_bib
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
from pzi.errors import PziError, exit_code_for_error
from pzi.export_service import export_bibtex, export_csv, export_json, export_ris
from pzi.promote_service import PromoteItem, PromoteResult, promote_bib
from pzi.search_service import SearchMatch, search_bib
from pzi.tag_service import TagChangeResult, TagListResult
from pzi.tag_service import add_tags as _add_tags_service
from pzi.tag_service import list_tags as _list_tags_service
from pzi.tag_service import remove_tags as _remove_tags_service
from pzi.update_service import UpdateBibResult, UpdatePlanItem, update_bib

__all__ = [
    "AddResult",
    "BibInfo",
    "CheckResult",
    "DedupeResult",
    "DeleteEntryResult",
    "EntryRecord",
    "EntrySummary",
    "MergeResult",
    "PromoteItem",
    "PromoteResult",
    "SearchMatch",
    "TagChangeResult",
    "TagListResult",
    "UpdateBibResult",
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

_EXPORTERS = {
    "bibtex": export_bibtex,
    "json": export_json,
    "csv": export_csv,
    "ris": export_ris,
}


#: The sort fields `bib_service.list_entries` actually implements. It falls back
#: to `citekey` for anything else *silently*, so an unvalidated typo returns
#: plausible data in the wrong order. The CLI enforces the same set as argparse
#: `choices` (`cli_parser.py:703`); this is that guard for the library.
_SORT_FIELDS = ("author", "citekey", "title", "year")

#: The bounds the other two front ends clamp to (`http_get_routes.py:150-151`,
#: `commands/entries.py:67`). Clamped rather than rejected, matching them.
_MIN_LIMIT, _MAX_LIMIT = 1, 500


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
    (`export_service.py:105-113`): a freshly ``pzi init``-ed config names a bib
    that does not exist until the first ``add``. The CLI prints those warnings
    and the HTTP envelope carries them — but a facade returning only the items
    dropped them, so a typo'd ``path =`` or an unmounted share came back as an
    empty list, indistinguishable from a library with nothing in it.

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
                f"{type(exc).__name__}: {exc}", code=exit_codes.ENVIRONMENT
            ) from exc

    return wrapper


def _unwrap(result: Mapping[str, Any], key: str) -> Any:
    """Return ``result[key]``, or raise what the CLI would have reported.

    `exit_code_for_error` is the CLI's own mapping from a service's structured
    `reason` to an exit code, reused here so a caller catching `PziError` sees
    the same classification a shell script branching on `$?` would.
    """
    if result.get("status") == "error":
        errors = [str(error) for error in result.get("errors") or []]
        message = str(result.get("message") or "") or (
            errors[0] if errors else "the command failed"
        )
        raise PziError(message, code=exit_code_for_error(result), details=errors)
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
    config_path: str | None = None,
    library: str | None = None,
) -> list[SearchMatch]:
    """Return entries matching the given filters, combined with AND.

    At least one filter is required. Matching is case-insensitive.
    """
    # Checked here rather than left to the service, which words the same refusal
    # as "provide at least one of --query, --author, --year, --tag" — flag names
    # a caller of this function never typed. This is the last such wording below
    # the facade: the unresolved-library messages named `--target` until they
    # were made flag-neutral in `config._unresolved_target_error`.
    if query is None and author is None and year is None and tag is None:
        raise PziError(
            "search needs at least one of query, author, year or tag", code=2
        )
    result = search_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        query=query,
        author=author,
        year=year,
        tag=tag,
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
) -> list[EntrySummary]:
    """Return a page of the library, sorted by ``citekey``, ``title``,
    ``author`` or ``year``."""
    if sort not in _SORT_FIELDS:
        raise PziError(
            f"unknown sort field {sort!r} — expected one of "
            f"{', '.join(_SORT_FIELDS)}",
            code=2,
        )
    result = list_entries(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        offset=max(0, offset),
        limit=max(_MIN_LIMIT, min(limit, _MAX_LIMIT)),
        sort=sort,
    )
    typed = dict(result)
    items = list(_unwrap(typed, "items"))
    _emit_warnings(typed)
    return items


@_public
def export(
    fmt: str = "bibtex",
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> str:
    """Return the whole library serialized as ``bibtex``, ``json``, ``csv`` or
    ``ris``."""
    exporter = _EXPORTERS.get(fmt)
    if exporter is None:
        raise PziError(
            f"unknown export format {fmt!r} — expected one of "
            f"{', '.join(sorted(_EXPORTERS))}",
            code=2,
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
) -> AddResult:
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
    overrides: dict[str, object] = {"tags": list(tags)} if tags else {}
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
    return typed


@_public
def check(
    *,
    strict: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> CheckResult:
    """Verify entries against metadata providers. Makes network requests."""
    result = check_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        strict=strict,
    )
    typed = result.copy()
    _unwrap(typed, "status")
    return typed


@_public
def dedupe(
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> DedupeResult:
    """Report exact duplicate clusters and fuzzy near-duplicates. Reads only."""
    typed = find_duplicates(bib_path=_bib_path(config_path, library)).copy()
    _unwrap(typed, "status")
    return typed


@_public
def promote(
    *,
    replace: bool = False,
    dry_run: bool = True,
    config_path: str | None = None,
    library: str | None = None,
) -> PromoteResult:
    """Find published versions of preprints. Makes network requests.

    **Previews by default** — pass ``dry_run=False`` to write. This sweeps the
    whole library and queries a provider per preprint, so a zero-argument call
    that wrote would rewrite thousands of entries over the network before the
    caller saw anything. `promote_bib` and ``POST /promote`` both preview by
    default for the same reason; the CLI writes because you typed the command.

    By default the preprint is kept and a published entry created beside it;
    ``replace=True`` updates the preprint in place instead.
    """
    result = promote_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        dry_run=dry_run,
        keep_preprint=not replace,
    )
    typed = result.copy()
    _unwrap(typed, "status")
    return typed


@_public
def delete(
    citekey: str,
    *,
    dry_run: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> DeleteEntryResult:
    """Delete one entry by citekey. Writes.

    A timestamped copy of the pre-delete library is made first, and its path is
    in the returned ``backup_path``. The entry's PDF is left on disk — the
    result reports it as ``pdf_path`` so a caller can remove it deliberately.

    Writes by default, unlike :func:`promote`. This deletes exactly the entry
    named in the call, where ``promote`` sweeps the whole library; the CLI's
    ``--force`` prompt guards a human typing at a terminal, not a script that
    wrote the citekey out.
    """
    typed = delete_entry(
        bib_path=_bib_path(config_path, library),
        citekey=citekey,
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    return typed


@_public
def add_tags(
    citekey: str,
    tags: list[str],
    *,
    dry_run: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> TagChangeResult:
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
        tags=list(tags),
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    return typed


@_public
def remove_tags(
    citekey: str,
    tags: list[str],
    *,
    dry_run: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> TagChangeResult:
    """Remove tags from one entry. Writes.

    The counterpart of :func:`add_tags`, with the same result shape. Removing a
    tag the entry does not have leaves ``changed`` false.
    """
    typed = _remove_tags_service(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        citekey=citekey,
        tags=list(tags),
        dry_run=dry_run,
    ).copy()
    _unwrap(typed, "status")
    return typed


@_public
def merge(
    citekey_a: str,
    citekey_b: str,
    *,
    dry_run: bool = True,
    config_path: str | None = None,
    library: str | None = None,
) -> MergeResult:
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
    return typed


@_public
def list_tags(
    citekey: str | None = None,
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> TagListResult:
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
    return typed


@_public
def update(
    *,
    dry_run: bool = True,
    config_path: str | None = None,
    library: str | None = None,
) -> UpdateBibResult:
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
    return typed
