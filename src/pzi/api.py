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

import os
from typing import Any

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


def _home() -> str:
    """The real home directory.

    Deliberately not a parameter. `home_dir` exists on every service function so
    tests can point config resolution at a temp directory; a library caller has
    no reason to override where their own home is, and exposing it would freeze
    a test seam as public API.
    """
    return os.path.expanduser("~")


def _config_path(config: str | None) -> str:
    return config if config is not None else default_config_path(_home())


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


def _bib_path(config: str | None, library: str | None) -> str:
    """Resolve which library to act on, for the services that take a path.

    `export` and `dedupe` take a `bib_path` rather than a selector, so the
    resolution the other services do internally has to happen here.
    """
    resolved = load_bib_target(
        config_path=_config_path(config), home_dir=_home(), bib_selector=library
    )
    if isinstance(resolved, BibResolutionFailure):
        raise PziError(
            "; ".join(resolved.errors) or "could not resolve a library",
            details=list(resolved.errors),
        )
    _config, bib = resolved
    return bib["path"]


def search(
    query: str | None = None,
    *,
    author: str | None = None,
    year: int | None = None,
    tag: str | None = None,
    config: str | None = None,
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
        config_path=_config_path(config),
        home_dir=_home(),
        bib_selector=library,
        query=query,
        author=author,
        year=year,
        tag=tag,
    )
    return list(_unwrap(dict(result), "matches"))


def entries(
    *,
    offset: int = 0,
    limit: int = 50,
    sort: str = "citekey",
    config: str | None = None,
    library: str | None = None,
) -> list[dict[str, Any]]:
    """Return a page of the library, sorted by ``citekey``, ``title``,
    ``author`` or ``year``."""
    result = list_entries(
        config_path=_config_path(config),
        home_dir=_home(),
        bib_selector=library,
        offset=offset,
        limit=limit,
        sort=sort,
    )
    return list(_unwrap(dict(result), "items"))


def export(
    fmt: str = "bibtex",
    *,
    config: str | None = None,
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
    return str(_unwrap(dict(exporter(_bib_path(config, library))), "content"))


def add(
    source: str,
    *,
    tags: list[str] | None = None,
    dry_run: bool = False,
    force_new: bool = False,
    config: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Capture a paper by DOI, URL or local PDF path.

    Returns the write result: the citekey, whether it was an insert or an
    update, the PDF path if one was attached, and any warnings. Makes network
    requests.
    """
    overrides: dict[str, object] = {"tags": list(tags)} if tags else {}
    result = add_input_to_bib(
        config_path=_config_path(config),
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


def check(
    *,
    strict: bool = False,
    config: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Verify entries against metadata providers. Makes network requests."""
    result = check_bib(
        config_path=_config_path(config),
        home_dir=_home(),
        bib_selector=library,
        strict=strict,
    )
    typed = dict(result)
    _unwrap(typed, "status")
    return typed


def dedupe(
    *,
    config: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Report exact duplicate clusters and fuzzy near-duplicates. Reads only."""
    typed = dict(find_duplicates(bib_path=_bib_path(config, library)))
    _unwrap(typed, "status")
    return typed


def promote(
    *,
    replace: bool = False,
    dry_run: bool = False,
    config: str | None = None,
    library: str | None = None,
) -> dict[str, Any]:
    """Find published versions of preprints. Makes network requests.

    By default the preprint is kept and a published entry created beside it;
    ``replace=True`` updates the preprint in place instead.
    """
    result = promote_bib(
        config_path=_config_path(config),
        home_dir=_home(),
        bib_selector=library,
        dry_run=dry_run,
        keep_preprint=not replace,
    )
    typed = dict(result)
    _unwrap(typed, "status")
    return typed
