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
from collections.abc import Callable
from typing import Any

from pzi import exit_codes
from pzi.add_service import add_input_to_bib
from pzi.bib_service import list_entries
from pzi.check_service import check_bib
from pzi.commands.common import exit_code_for_error
from pzi.config import BibResolutionFailure, default_config_path, load_bib_target
from pzi.dedupe_service import find_duplicates
from pzi.errors import PziError
from pzi.export_service import export_bibtex, export_csv, export_json, export_ris
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib

__all__ = [
    "add",
    "check",
    "dedupe",
    "entries",
    "export",
    "promote",
    "search",
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


def _emit_warnings(result: dict[str, Any]) -> None:
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
    """
    for warning in result.get("warnings") or []:
        warnings.warn(str(warning), UserWarning, stacklevel=3)


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


def _unwrap(result: dict[str, Any], key: str) -> Any:
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


def _bib_path(config_path: str | None, library: str | None) -> str:
    """Resolve which library to act on, for the services that take a path.

    `export` and `dedupe` take a `bib_path` rather than a selector, so the
    resolution the other services do internally has to happen here.
    """
    resolved = load_bib_target(
        config_path=_resolved_config_path(config_path), home_dir=_home(), bib_selector=library
    )
    if isinstance(resolved, BibResolutionFailure):
        raise PziError(
            "; ".join(resolved.errors) or "could not resolve a library",
            details=list(resolved.errors),
        )
    _config, bib = resolved
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
) -> list[dict[str, Any]]:
    """Return entries matching the given filters, combined with AND.

    At least one filter is required. Matching is case-insensitive.
    """
    # Checked here rather than left to the service, which words the same refusal
    # as "provide at least one of --query, --author, --year, --tag" — flag names
    # a caller of this function never typed. Everything *below* the facade still
    # speaks CLI, and that is a known wart rather than a solved problem: an
    # unresolvable `library=` still reports itself as `--target`.
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
) -> list[dict[str, Any]]:
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
def add(
    source: str,
    *,
    tags: list[str] | None = None,
    dry_run: bool = False,
    force_new: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Capture a paper by DOI, URL or local PDF path.

    Returns the write result: the citekey, whether it was an insert or an
    update, the PDF path if one was attached, and any warnings. Makes network
    requests.
    """
    overrides: dict[str, object] = {"tags": list(tags)} if tags else {}
    result = add_input_to_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        value=source,
        record_overrides=overrides,
        bib_selector=library,
        dry_run=dry_run,
        force_new=force_new,
    )
    typed = dict(result)
    _unwrap(typed, "status")
    return typed


@_public
def check(
    *,
    strict: bool = False,
    config_path: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Verify entries against metadata providers. Makes network requests."""
    result = check_bib(
        config_path=_resolved_config_path(config_path),
        home_dir=_home(),
        bib_selector=library,
        strict=strict,
    )
    typed = dict(result)
    _unwrap(typed, "status")
    return typed


@_public
def dedupe(
    *,
    config_path: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Report exact duplicate clusters and fuzzy near-duplicates. Reads only."""
    typed = dict(find_duplicates(bib_path=_bib_path(config_path, library)))
    _unwrap(typed, "status")
    return typed


@_public
def promote(
    *,
    replace: bool = False,
    dry_run: bool = True,
    config_path: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
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
    typed = dict(result)
    _unwrap(typed, "status")
    return typed
