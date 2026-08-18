"""The three front ends, compared to each other — item 429.

`test_cli_surface`, `test_http_route_inventory` and `test_public_api` each pin
one surface in isolation, so nothing noticed when the *matrix between* them
drifted — and it did: `promote`'s dry-run default disagreed across surfaces
until someone happened to read all three.

This file declares the matrix once. Every CLI command, every HTTP route and
every public Python function must appear in it, and a capability missing from a
surface must say why. That is the property worth having: **a gap becomes a
decision rather than an oversight.**
"""

from __future__ import annotations

import argparse
import inspect
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

import pzi
from pzi.cli_parser import build_parser
from pzi.http_get_routes import BINARY_GET_ROUTES, GET_PREFIX_ROUTES, GET_ROUTES
from pzi.http_post_routes import POST_ROUTES, process_post_request


@dataclass(frozen=True)
class Capability:
    """One thing pzi can do, and where each front end offers it."""

    name: str
    #: The parser command path, e.g. `"library check"`. Several capabilities may
    #: name the same one — `update` and `promote` are one command and a flag.
    cli: str | None = None
    http: tuple[str, ...] = ()
    python: str | None = None
    #: Required whenever a surface is missing. An empty cell with no reason is
    #: the thing this file exists to stop.
    note: str = ""
    #: Extra CLI spelling, when the command alone does not identify it.
    cli_detail: str = ""


_MATRIX: tuple[Capability, ...] = (
    Capability("capture", cli="add", http=("/capture",), python="add"),
    Capability("search", cli="search", http=("/search",), python="search"),
    Capability(
        "list entries",
        cli="entries",
        cli_detail="pzi entries",
        http=("/entries",),
        python="entries",
    ),
    Capability(
        "entry detail",
        cli="entries",
        cli_detail="pzi entries <citekey>",
        http=("/detail/",),
        python="get",
    ),
    Capability("list libraries", cli="library list", http=("/bibs",), python="list_bibs"),
    Capability(
        "export",
        cli="export",
        http=("/export", "/export/raw"),
        python="export",
        note=(
            "two routes because the JSON envelope and the raw document are "
            "different answers; the CLI and Python return the document"
        ),
    ),
    Capability("read tags", cli="tag list", http=("/tags", "/tags/"), python="list_tags"),
    Capability("add tags", cli="tag add", http=("/tags/add",), python="add_tags"),
    Capability(
        "remove tags", cli="tag remove", http=("/tags/remove",), python="remove_tags"
    ),
    Capability(
        "update metadata",
        cli="update",
        cli_detail="pzi update",
        http=("/update",),
        python="update",
    ),
    Capability(
        "promote preprints",
        cli="update",
        cli_detail="pzi update --promote",
        http=("/promote",),
        python="promote",
    ),
    Capability("delete an entry", cli="delete", http=("/delete",), python="delete"),
    Capability(
        "audit references",
        cli="library check",
        python="check",
        note=(
            "no HTTP route: an audit is a long multi-provider network sweep with "
            "no progress channel, so it would sit past any browser-extension "
            "timeout with nothing to show for the wait"
        ),
    ),
    Capability(
        "report duplicates",
        cli="library dedupe",
        python="dedupe",
        note="no HTTP route: nothing in the extension acts on duplicates",
    ),
    Capability(
        "merge two entries",
        cli="library merge",
        python="merge",
        note="no HTTP route: the extension captures, it does not reorganise",
    ),
    Capability(
        "check integrity / relocate orphan PDFs",
        cli="library clean",
        note="CLI-only: a maintenance sweep over the whole library and papers dir",
    ),
    Capability(
        "audit and rename citekeys",
        cli="library reindex",
        note=(
            "CLI-only: --rename-citekeys breaks every \\cite{} using the old keys, "
            "so it stays behind a typed command with a confirmation"
        ),
    ),
    Capability(
        "import a .bib",
        cli="import",
        note=(
            "CLI-only: reads an *arbitrary* local path. The one HTTP route that "
            "takes a path, /inbox/drain, confines it to the configured "
            "inbox_path and is disabled without one; a .bib to import has no "
            "such pre-declared location"
        ),
    ),
    Capability(
        "drain an inbox file",
        cli="inbox",
        http=("/inbox/drain",),
        note="no Python function: a batch capture loop a script would write itself",
    ),
    Capability(
        "attach a PDF",
        cli="pdf attach",
        http=("/attach-pdf-bytes", "/attach-pdf-raw"),
        note=(
            "no Python function: the two routes exist for the extension to hand "
            "over bytes from an authenticated browser session"
        ),
    ),
    Capability(
        "retry a PDF fetch",
        cli="pdf retry",
        note="no HTTP or Python: a CLI recovery step over entries that have no PDF",
    ),
    Capability(
        "serve a stored PDF",
        http=("/pdf/",),
        note=(
            "HTTP-only: the extension opens it in a tab. `pzi.get()` returns the "
            "path, which is what a local caller needs"
        ),
    ),
    Capability(
        "discover a PDF in a page",
        http=("/browser/discover",),
        note="HTTP-only: the extension's own capture flow",
    ),
    Capability(
        "download through the browser",
        http=("/browser/download",),
        note="HTTP-only: drives an authenticated browser session the extension owns",
    ),
    Capability(
        "health / diagnostics",
        cli="doctor",
        http=("/health",),
        note=(
            "no Python function: it reports on the installation, not the library, "
            "and a caller who imported pzi already knows it is installed"
        ),
    ),
    Capability(
        "run the HTTP server",
        cli="server",
        note="CLI-only: it *is* the HTTP surface, and blocks",
    ),
    Capability(
        "create the configuration",
        cli="init",
        note="CLI-only: it writes the config both other surfaces read",
    ),
)


def _parser_commands() -> set[str]:
    """Every command path a user can type, e.g. `entries`, `library check`."""

    def subparsers(parser: argparse.ArgumentParser) -> dict:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return dict(action.choices)
        return {}

    found: set[str] = set()
    for name, sub in subparsers(build_parser()).items():
        children = subparsers(sub)
        found.update(f"{name} {child}" for child in children)
        if not children:
            found.add(name)
    return found


def _http_routes() -> set[str]:
    """The same registries `test_http_route_inventory` reads."""
    return {
        *(route.path for route in GET_ROUTES),
        *(route.prefix for route in GET_PREFIX_ROUTES),
        *(route.path for route in BINARY_GET_ROUTES),
        *(route.path for route in POST_ROUTES),
    }


def _python_functions() -> set[str]:
    return {
        name
        for name in pzi.__all__
        if name[0].islower() and name != "__version__" and callable(getattr(pzi, name))
    }


def test_every_cli_command_is_in_the_matrix() -> None:
    declared = {cap.cli for cap in _MATRIX if cap.cli}
    actual = _parser_commands()
    assert actual - declared == set(), (
        f"CLI commands missing from the matrix: {sorted(actual - declared)} — "
        "add a row saying which other surfaces offer them, and why not the rest"
    )
    assert declared - actual == set(), (
        f"the matrix names CLI commands that do not exist: {sorted(declared - actual)}"
    )


def test_every_http_route_is_in_the_matrix() -> None:
    declared = {path for cap in _MATRIX for path in cap.http}
    actual = _http_routes()
    assert actual - declared == set(), (
        f"HTTP routes missing from the matrix: {sorted(actual - declared)}"
    )
    assert declared - actual == set(), (
        f"the matrix names routes that do not exist: {sorted(declared - actual)}"
    )


def test_every_public_function_is_in_the_matrix() -> None:
    declared = {cap.python for cap in _MATRIX if cap.python}
    actual = _python_functions()
    assert actual - declared == set(), (
        f"public API functions missing from the matrix: {sorted(actual - declared)}"
    )
    assert declared - actual == set(), (
        f"the matrix names functions that do not exist: {sorted(declared - actual)}"
    )


def test_no_route_or_function_is_claimed_twice() -> None:
    """One home per route and per function, or the matrix is not a partition."""
    routes = [path for cap in _MATRIX for path in cap.http]
    assert len(routes) == len(set(routes)), "a route appears in two rows"
    functions = [cap.python for cap in _MATRIX if cap.python]
    assert len(functions) == len(set(functions)), "a function appears in two rows"


def test_rows_sharing_a_cli_command_say_which_invocation_they_are() -> None:
    """Two capabilities on one command have to be told apart.

    `pzi update` is both "fill gaps" and, with `--promote`, "replace preprints";
    `pzi entries` is both the listing and one entry's detail. Without the
    spelling written down, the matrix says a command appears twice and leaves
    the reader to guess why — and `cli_detail` would be a field nothing reads.
    """
    from collections import Counter

    shared = {
        command
        for command, count in Counter(
            cap.cli for cap in _MATRIX if cap.cli
        ).items()
        if count > 1
    }
    missing = [
        cap.name for cap in _MATRIX if cap.cli in shared and not cap.cli_detail
    ]
    assert not missing, (
        f"these rows share a CLI command but do not name their invocation: {missing}"
    )


def test_every_gap_is_explained() -> None:
    """The point of the file: a missing cell has to be a decision.

    Without this the matrix would just be a list, and a capability quietly
    added to one surface would sit there looking intentional.
    """
    unexplained = [
        cap.name
        for cap in _MATRIX
        if not cap.note and not (cap.cli and cap.http and cap.python)
    ]
    assert not unexplained, (
        "these capabilities are missing from a surface with no reason given: "
        f"{unexplained}"
    )


# --- The drift the item was named for ----------------------------------------


def _cli_dry_run_default(command: str) -> bool:
    """What `--dry-run` defaults to for a command path, from the real parser."""
    parser = build_parser()
    for name in command.split():
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                parser = action.choices[name]
                break
    for action in parser._actions:
        if "--dry-run" in action.option_strings:
            return bool(action.default)
    raise AssertionError(f"`pzi {command}` has no --dry-run")


def _http_dry_run_default(
    route: str,
    service: str,
    body: dict[str, object] | None = None,
    *,
    config_path: str = "/nonexistent/config.toml",
    home_dir: str = "/nonexistent",
) -> bool:
    """What the route passes for `dry_run` given a body that omits it.

    Observed by calling the route with the service patched, rather than by
    reading the handler's source — the same reason the route inventory stopped
    grepping `http_api`.

    *body* and *config_path* carry whatever else the route validates before it
    reaches the service — `/delete` needs a citekey *and* a resolvable library,
    where `/update` hands both straight to the service. Without them the call
    400s early and the stub is never reached, which would make the caller's
    assertion pass by not running. So this asserts rather than skips: a route
    listed in the table and silently never probed is precisely the failure this
    file exists to prevent.
    """
    with patch(f"pzi.http_post_routes.{service}") as stub:
        stub.return_value = {"status": "ok", "items": [], "errors": []}
        process_post_request(route, dict(body or {}), config_path, home_dir)
        assert stub.called, (
            f"{route} refused the body before reaching {service}; give "
            "`_http_dry_run_default` whatever the route validates first"
        )
        return bool(stub.call_args.kwargs["dry_run"])


@pytest.mark.parametrize(
    ("cli_command", "route", "service", "python_name", "expected", "body"),
    [
        # (CLI, HTTP, Python) — expected dry-run default per surface. The CLI
        # writes because you typed the command; the other two preview.
        ("update", "/update", "update_bib", "update", (False, True, True), None),
        # `merge` has no route, so only two surfaces have a default to state.
        ("library merge", None, None, "merge", (False, None, True), None),
        # `delete` is the exception to the CLI half of the rule, and the row
        # that exists because it was wrong: the CLI's `--dry-run` defaults to
        # False but the command *refuses* without `--force`, so no surface
        # deletes on a bare invocation. The Python API used to (decision 34).
        # The route validates a citekey first, so the probe has to carry one.
        (
            "delete", "/delete", "delete_entry", "delete", (False, True, True),
            {"citekey": "smith2020"},
        ),
    ],
)
def test_the_dry_run_defaults_are_the_declared_ones(
    cli_command: str, route: str | None, service: str | None,
    python_name: str, expected: tuple[bool | None, bool | None, bool | None],
    body: dict[str, object] | None, tmp_path: Path,
) -> None:
    """Each surface's preview default, asserted rather than assumed.

    They are **deliberately not uniform** (decision 23). The CLI acts because
    you typed the command; the Python API previews because a zero-argument call
    from a script should not sweep a library before the caller sees anything.
    Recording that here is the point — a test demanding agreement would be
    wrong, and one ignoring the question would be useless.

    `delete` reads as an exception and is not one. Its CLI `--dry-run` defaults
    to False, but `commands/delete.py` refuses outright without `--force`, so
    the effective answer on every surface is "not without a second word".
    Decision 34 moved the Python default to match, after 23's rule ("a function
    naming its target acts") made this the one surface that destroyed an entry
    on the first call.
    """
    want_cli, want_http, want_python = expected
    assert _cli_dry_run_default(cli_command) is want_cli
    if route is not None and service is not None:
        bib_path = tmp_path / "ml.bib"
        bib_path.write_text("", encoding="utf-8")
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
            encoding="utf-8",
        )
        assert (
            _http_dry_run_default(
                route, service, body,
                config_path=str(config_path), home_dir=str(tmp_path),
            )
            is want_http
        )
    signature = inspect.signature(getattr(pzi, python_name))
    assert signature.parameters["dry_run"].default is want_python


def test_promote_previews_on_both_non_cli_surfaces() -> None:
    """The specific drift item 429 cites.

    `promote` is a flag on `pzi update`, not its own command, so it has no
    `--dry-run` of its own to check — the two surfaces that *do* take a default
    are HTTP and Python, and they must agree.
    """
    assert _http_dry_run_default("/promote", "promote_bib") is True
    assert inspect.signature(pzi.promote).parameters["dry_run"].default is True
