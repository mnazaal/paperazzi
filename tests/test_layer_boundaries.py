"""Architectural guard: the core/planning layer must not import front-ends.

pzi keeps core planning logic separate from the side-effecting front-ends
(CLI, HTTP API, browser hooks). That split is otherwise only a convention
enforced by review; this test makes it mechanical.

Note: the tier names below describe import *layering*, not functional
purity — CORE modules may still do real I/O (DNS resolution, HTTP fetches,
disk reads for a single file). What CORE guarantees is narrower: no
front-end or browser-hook module is anywhere in the dependency chain.

Every pzi/*.py module is classified into exactly one of five tiers:

  CORE      — no front-end AND no browser imports, even transitively.
  PIPELINE  — may reach browser hooks (PDF/discovery), never front-end.
  SERVICE   — service-layer modules; no *direct* front-end imports.
  FRONTEND  — CLI, commands, HTTP API layers.
  BROWSER   — browser/server-browser hook modules.

Any module not in any tier fails test_all_modules_classified(), forcing an
explicit decision when a new file is added (no silent drift).

The guard checks:
  • Relative imports (``from . import x``) are resolved within pzi, so a
    back-edge via a relative import is caught the same as an absolute one.
  • CORE: transitive closure must not reach FRONTEND or BROWSER.
  • PIPELINE: transitive closure must not reach FRONTEND.
  • SERVICE: direct imports must not include FRONTEND modules.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import pzi

_SRC = Path(pzi.__file__).parent

# ---------------------------------------------------------------------------
# Tier sets — every pzi/*.py module (except __init__ / __main__) belongs to
# exactly one of these.  Adding a file without updating the set fails the
# exhaustive-classification test.
# ---------------------------------------------------------------------------

CORE: frozenset[str] = frozenset(
    {
        # Core data / serialization / algorithm
        "bibtex",
        "similarity",
        "token_compare",
        "url_safety",
        "capture_models",
        "pdf_planning",
        "protocols",
        "bib_merge",
        "bib_serialize",
        "format_templates",
        "identifiers",
        "resolution_match",
        # Config / error / IO primitives
        "config",
        "errors",
        "exit_codes",
        "fileio",
        # HTTP / network utilities (no browser, no front-end)
        "fetch_helpers",
        "metadata_cache",
        "rate_limit",
        "safe_http",
        "flaresolverr",
        "translation_server",
        # Metadata / HTML utilities
        "capture_context",
        "html_metadata",
        "metadata_sources",
        # Promotion's candidate discovery, split out of `promote_service`.
        # CORE rather than SERVICE beside the service it came from: it only
        # queries providers and scores what they return, so nothing it imports
        # reaches a front end or a browser hook, and this graph keeps it so.
        "promote_planning",
        # The package root. It re-exports the public API, but *lazily* — nothing
        # is imported at module scope, so at load time it is a leaf, which is
        # what this graph measures. Eager re-export made it a front end that
        # every service transitively imported, i.e. a cycle.
        "__init__",
        # Pure planning helpers with no pzi deps
        "page_metadata_cmd",
        "pdf_acquisition_plan",
    }
)

# PDF/discovery pipeline — allowed to reach browser hooks, never front-end.
PIPELINE: frozenset[str] = frozenset(
    {
        "pdf",
        "pdf_discovery",
        "pdf_download",
    }
)

# Service layer — orchestrate work, may transitively reach browser via pipeline.
# Checked: no *direct* front-end imports.
SERVICE: frozenset[str] = frozenset(
    {
        "add_planning",
        "add_service",
        "bib_repository",
        "bib_service",
        "capture_core",
        "capture_local_pdf",
        "check_service",
        "clean_service",
        "dedupe_service",
        "doctor_service",
        "export_service",
        "import_service",
        "inbox_service",
        "node_runtime",
        "pdf_attach_session",
        "pdf_attach_session_store",
        "pdf_service",
        "promote_service",
        "reindex_service",
        "search_service",
        "setup_service",
        "tag_service",
        "ts_backend",
        "update_service",
    }
)

# Front-end / entrypoint layer.
FRONTEND: frozenset[str] = frozenset(
    {
        # The public Python API is a front end in exactly the sense the others
        # are: it sits on top of the services and translates their results for
        # one kind of caller. `cli` does it for a terminal, `http_api` for the
        # extension, `api` for `import pzi`.
        "api",
        "commands.__init__",
        "cli",
        "cli_json",
        "cli_parser",
        "cli_render",
        "cli_server",
        "http_api",
        "http_binary_routes",
        "http_get_routes",
        "http_payloads",
        "http_post_routes",
        "http_security",
        "http_status",
        # Every CLI command runner. These were invisible to this guard until the
        # module scan became recursive.
        "commands.add",
        "commands.bibs",
        "commands.check",
        "commands.clean",
        "commands.common",
        "commands.dedupe",
        "commands.delete",
        "commands.doctor",
        "commands.entries",
        "commands.export",
        "commands.import_",
        "commands.inbox",
        "commands.init",
        "commands.library",
        "commands.pdf",
        "commands.reindex",
        "commands.search",
        "commands.server",
        "commands.tags",
        "commands.update",
    }
)

# Browser / server-browser hook modules.
BROWSER: frozenset[str] = frozenset(
    {
        "browser_pdf",
        "browser_pdf_hook",
        "browser_session",
        "browser_session_manager",
        "server_browser",
    }
)

# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


class _ModuleLevel(ast.Module):
    """A view of *tree* with function bodies removed.

    Class bodies are kept: they execute at import time, so an import written
    there is a real load-time edge.
    """

    def __init__(self, tree: ast.Module) -> None:
        super().__init__(body=[*_module_level_nodes(tree.body)], type_ignores=[])


def _is_type_checking_guard(node: ast.stmt) -> bool:
    """``if TYPE_CHECKING:`` — a block that never runs.

    It is module level, so the plain body filter keeps it, but the runtime
    never executes it: that is the entire reason the guard exists. Counting its
    imports as load-time edges reported cycles that cannot happen — and would
    have condemned the lazy public API, whose whole point is that the edge is
    not there at runtime.
    """
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _module_level_nodes(body: list[ast.stmt]) -> list[ast.stmt]:
    kept: list[ast.stmt] = []
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if _is_type_checking_guard(node):
            continue
        if isinstance(node, ast.ClassDef):
            kept.append(ast.ClassDef(
                name=node.name, bases=[], keywords=[],
                body=_module_level_nodes(node.body), decorator_list=[],
            ))
            continue
        kept.append(node)
    return kept


def _package_of(path: Path) -> tuple[str, ...]:
    """The dotted package parts containing *path*, relative to the pzi package."""
    return path.relative_to(_SRC).parent.parts


def _imported_pzi_modules(path: Path) -> set[str]:
    """Return candidate pzi-internal module stems imported directly by *path*.

    Handles absolute (``from pzi.foo import …``), package-level
    (``from pzi import foo``) and relative (``from . import foo`` /
    ``from .foo import …``) forms so a back-edge is caught whichever way it is
    written.  The package-level and bare-relative forms cannot be told apart
    from a re-exported function or type at this level (``from pzi import
    cli_version_text`` parses identically to ``from pzi import http_api``), so
    the names they yield are candidates; :func:`_build_import_graph` drops the
    ones that are not real modules.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    # Module level only. An import inside a function body runs on *call*, not on
    # import, so it cannot take part in an import cycle — which is exactly why
    # several modules already use one deliberately to break one (see
    # `format_templates.describe_bbt_formula_error`, and `pzi.__init__`'s lazy
    # public API). Walking every node counted those as load-time edges and
    # reported cycles that cannot happen.
    for node in ast.walk(_ModuleLevel(tree)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pzi."):
                    names.add(alias.name.removeprefix("pzi."))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module.startswith("pzi."):
                    names.add(node.module.removeprefix("pzi."))
                elif node.module == "pzi":
                    # ``from pzi import foo`` — the names *are* the stems.
                    for alias in node.names:
                        names.add(alias.name)
                    # …and the package root itself, always. Importing *any*
                    # name from `pzi` executes `pzi/__init__.py`, so this is a
                    # real edge whether or not the name is also a module. Only
                    # the names were recorded, so `from pzi import
                    # cli_version_text` — a re-exported function — produced no
                    # edge at all, and the cycle back through `__init__` was
                    # invisible even once `__init__` became a node.
                    names.add("__init__")
            elif node.level > 0:
                # A relative import inside the pzi package. Resolved against the
                # *importing module's* package, which is what makes it comparable
                # with the dotted names the rest of the graph uses: from
                # `pzi/commands/add.py`, `from .common import x` is
                # `commands.common`, not `common`. Taking the bare stem produced a
                # name matching no module, so `_build_import_graph` dropped the edge
                # and a back-edge written this way was invisible to this guard.
                package = list(_package_of(path))
                # `level` 1 means "this package", 2 the parent, and so on.
                for _ in range(node.level - 1):
                    if package:
                        package.pop()
                prefix = ".".join(package)
                if node.module:
                    names.add(f"{prefix}.{node.module}" if prefix else node.module)
                else:
                    for alias in node.names:
                        names.add(f"{prefix}.{alias.name}" if prefix else alias.name)

    return names


def _all_imported_names(path: Path) -> set[str]:
    """Every module *path* imports, anywhere in the file — pzi or third-party.

    Two differences from :func:`_imported_pzi_modules`, both because this feeds
    a purity claim rather than a load-order one. It keeps non-pzi names, since
    ``import os`` is the thing being looked for; and it walks function bodies,
    since a deferred ``import os`` is still a route to the disk even though it
    is not a load-time edge.

    pzi names come back fully dotted (``pzi.bibtex``), which is what tells them
    apart from stdlib here.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
                if node.module == "pzi":
                    names.update(f"pzi.{alias.name}" for alias in node.names)
            elif node.level > 0:
                package = list(_package_of(path))
                for _ in range(node.level - 1):
                    if package:
                        package.pop()
                prefix = ".".join(["pzi", *package])
                if node.module:
                    names.add(f"{prefix}.{node.module}")
                else:
                    names.update(f"{prefix}.{alias.name}" for alias in node.names)
    return names


def _module_name(path: Path) -> str:
    """Dotted name of a module file relative to the pzi package."""
    relative = path.relative_to(_SRC).with_suffix("")
    return ".".join(relative.parts)


def _all_module_paths() -> list[Path]:
    """Every pzi module file, including subpackages such as `commands/`.

    Recursive on purpose: a non-recursive glob left all of `commands/`
    unclassified and unchecked, so a command reaching straight past the service
    layer would have passed silently.
    """
    # `__init__` is included. Excluding it made this file blind to the one
    # cycle the package actually had — `pzi.__init__ -> pzi.api ->
    # pzi.commands.common -> pzi.cli_parser -> pzi.__init__` — because the
    # module at one end of it was not a node in the graph. It is also a
    # front-end module now that it re-exports the public API, so it needs
    # classifying like any other. `__main__` stays out: it is the `python -m`
    # shim, imports `cli` and nothing imports it.
    paths = sorted(
        path for path in _SRC.rglob("*.py") if path.stem != "__main__"
    )
    # Every boundary test below is of the form `assert not offenders`, which an
    # empty scan satisfies vacuously. They were protected only by
    # `test_all_modules_classified` failing first — a sibling, so a reordering
    # or a `-k` selection could run the four invariants against nothing and
    # report four passes. Assert the walk found something, at the walk.
    assert paths, f"module scan found no modules under {_SRC} — the boundary tests would pass vacuously"
    return paths


def _build_import_graph() -> dict[str, set[str]]:
    """Parse every pzi module and return name → {directly-imported modules}.

    Candidate names that do not correspond to a real module file are dropped:
    ``from pzi import cli_version_text`` names a re-exported function, not an
    edge, and leaving it in would put a phantom node in the graph.
    """
    paths = _all_module_paths()
    known = {_module_name(path) for path in paths}
    graph: dict[str, set[str]] = {}
    for path in paths:
        graph[_module_name(path)] = _imported_pzi_modules(path) & known
    return graph


def _transitive_deps(start: str, graph: dict[str, set[str]]) -> set[str]:
    """BFS reachable pzi stems from *start* (not including *start*)."""
    visited: set[str] = set()
    queue: deque[str] = deque(graph.get(start, ()))
    while queue:
        mod = queue.popleft()
        if mod in visited:
            continue
        visited.add(mod)
        queue.extend(graph.get(mod, ()))
    return visited


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_modules_classified() -> None:
    """Every pzi/*.py module (except __init__/__main__) is in exactly one tier.

    Fails when a new module is added without being classified — no silent drift.
    """
    all_modules = {_module_name(p) for p in _all_module_paths()}
    all_tiers = CORE | PIPELINE | SERVICE | FRONTEND | BROWSER

    unclassified = all_modules - all_tiers
    assert not unclassified, (
        f"unclassified pzi modules (add to one tier in test_layer_boundaries.py): "
        f"{sorted(unclassified)}"
    )

    # Also assert no module appears in more than one tier (tiers are disjoint).
    tier_list = [CORE, PIPELINE, SERVICE, FRONTEND, BROWSER]
    for i, tier_a in enumerate(tier_list):
        for tier_b in tier_list[i + 1 :]:
            overlap = tier_a & tier_b
            assert not overlap, f"module appears in multiple tiers: {sorted(overlap)}"


def test_strict_pure_no_frontend_or_browser_transitively() -> None:
    """CORE modules must not reach FRONTEND or BROWSER, even transitively.

    A single-hop back-edge (``capture_core`` → some helper → ``http_api``) is
    caught here where the old direct-only check would have missed it.
    """
    graph = _build_import_graph()
    forbidden = FRONTEND | BROWSER
    offenders: dict[str, list[str]] = {}
    for mod in CORE:
        reached = _transitive_deps(mod, graph) & forbidden
        if reached:
            offenders[mod] = sorted(reached)
    assert not offenders, (
        "CORE modules transitively import FRONTEND or BROWSER:\n"
        + "\n".join(f"  {m} → {deps}" for m, deps in sorted(offenders.items()))
    )


def test_pipeline_no_frontend_transitively() -> None:
    """PIPELINE modules may reach BROWSER (PDF hooks) but never FRONTEND."""
    graph = _build_import_graph()
    offenders: dict[str, list[str]] = {}
    for mod in PIPELINE:
        reached = _transitive_deps(mod, graph) & FRONTEND
        if reached:
            offenders[mod] = sorted(reached)
    assert not offenders, (
        "PIPELINE modules transitively import FRONTEND:\n"
        + "\n".join(f"  {m} → {deps}" for m, deps in sorted(offenders.items()))
    )


def test_service_no_direct_frontend_imports() -> None:
    """SERVICE modules must not directly import FRONTEND modules.

    Service-layer code may orchestrate PDF/browser work (via PIPELINE), but
    must never pull in CLI or HTTP routing code directly.
    """
    graph = _build_import_graph()
    offenders: dict[str, list[str]] = {}
    for mod in SERVICE:
        bad = graph.get(mod, set()) & FRONTEND
        if bad:
            offenders[mod] = sorted(bad)
    assert not offenders, (
        "SERVICE modules directly import FRONTEND:\n"
        + "\n".join(f"  {m} → {deps}" for m, deps in sorted(offenders.items()))
    )


def test_no_import_cycles() -> None:
    """No pzi module may participate in an import cycle.

    Function-level imports count: the AST walk sees them wherever they sit, and
    a lazy import is how a cycle usually hides. Two existed before this guard —
    `pdf` <-> `pdf_download` (the byte-storage helpers sat on the wrong side of
    the boundary) and `bib_repository` <-> `promote_service` (the repository
    reached up into a service for a pure preprint classifier).
    """
    graph = _build_import_graph()
    cycles: list[list[str]] = []
    # Iterative DFS with an explicit stack, recording the first cycle found per
    # start node; enough to name the offenders without listing every rotation.
    for start in sorted(graph):
        stack: list[tuple[str, list[str]]] = [(start, [start])]
        seen: set[str] = set()
        while stack:
            node, path = stack.pop()
            for dep in sorted(graph.get(node, ())):
                if dep == start:
                    cycles.append([*path, dep])
                    stack.clear()
                    break
                if dep in seen or dep not in graph:
                    continue
                seen.add(dep)
                stack.append((dep, [*path, dep]))

    assert not cycles, "import cycles:\n" + "\n".join(" -> ".join(c) for c in cycles)


# ---------------------------------------------------------------------------
# `bib_merge` is purer than its tier. CORE is a layering promise, not a purity
# one — `config`, `fileio` and `safe_http` are all CORE and all touch the
# outside world — so membership above does not say what this module claims.
# ---------------------------------------------------------------------------

#: Modules through which a Python process reaches the outside world. Not
#: exhaustive over the stdlib, and it does not need to be: it names the routes
#: the rest of pzi actually uses to read a bib, take a lock, run a browser or
#: fetch metadata, which are the ones the merge cluster must not acquire.
#: `urllib.parse` is deliberately absent — `identifiers` uses it for string
#: surgery on DOIs and URLs, and the I/O lives in `urllib.request`.
_IO_MODULES = frozenset(
    {
        "asyncio",
        "fcntl",
        "fileinput",
        "ftplib",
        "glob",
        "http",
        "io",
        "mmap",
        "multiprocessing",
        "os",
        "pathlib",
        "select",
        "selectors",
        "shutil",
        "smtplib",
        "socket",
        "sqlite3",
        "ssl",
        "subprocess",
        "tempfile",
        "urllib.request",
        "webbrowser",
        # Third-party, all of which read or write something.
        "bibtexparser",
        "httpx",
        "playwright",
        "portalocker",
        "pypdf",
        "requests",
        "urllib3",
    }
)

#: Everything `bib_merge` imports, pinned. Equality rather than a subset check:
#: the point of the extraction is that a new dependency here is a decision, and
#: a subset check would wave through the one import that ends the purity.
_BIB_MERGE_IMPORTS = frozenset(
    {
        "__future__",
        "typing",
        "pzi.bibtex",
        "pzi.identifiers",
        "pzi.similarity",
    }
)


def _reaches_io(name: str) -> bool:
    return any(name == mod or name.startswith(f"{mod}.") for mod in _IO_MODULES)


def _bib_merge_closure() -> dict[str, Path]:
    """`bib_merge` and every pzi module reachable from it, by any import."""
    start = _SRC / "bib_merge.py"
    reached: dict[str, Path] = {}
    queue: deque[Path] = deque([start])
    while queue:
        path = queue.popleft()
        name = _module_name(path)
        if name in reached:
            continue
        reached[name] = path
        for imported in _all_imported_names(path):
            # The bare package name, from `from pzi import exit_codes`. Not an
            # edge worth following: `pzi/__init__.py` imports nothing at module
            # scope (that is the fix that broke the API cycle), and its lazy
            # `__getattr__` is the public front end, which nothing in this
            # closure calls. The dotted sibling name in the same statement is
            # the real edge and is followed below.
            if not imported.startswith("pzi.") or imported == "pzi":
                continue
            candidate = _SRC.joinpath(*imported.removeprefix("pzi.").split("."))
            candidate = candidate.with_suffix(".py")
            if candidate.exists():
                queue.append(candidate)
    return reached


def test_bib_merge_imports_exactly_its_three_pure_dependencies() -> None:
    """The merge cluster's whole import list, pinned.

    It was extracted from `bib_repository` precisely so that adding `fileio` or
    `pathlib` to it is a diff someone has to defend, rather than a line that
    reads like every other line in a 1,700-line module.
    """
    assert _all_imported_names(_SRC / "bib_merge.py") == _BIB_MERGE_IMPORTS


def test_nothing_bib_merge_reaches_can_perform_io() -> None:
    """No module in `bib_merge`'s closure opens a file, a socket or a process.

    This is the property the extraction exists to make mechanical: the merge
    decides what a write *will say* and `bib_repository` performs it, so a bug
    here rewrites the user's metadata with nothing raised anywhere. Checked
    over the transitive closure, function bodies included — a deferred `import
    os` reaches the disk just as well as a top-level one — and `open()` is
    checked directly, since it needs no import at all.
    """
    closure = _bib_merge_closure()
    # The walk is the whole check, and `assert not offenders` passes vacuously
    # on an empty one. `errors` is two hops out (via `bibtex`), so requiring it
    # pins that the walk is transitive and not just a direct-import scan.
    assert {"bib_merge", "bibtex", "errors"} <= set(closure), (
        f"the closure walk is not reaching transitively: {sorted(closure)}"
    )

    offenders: dict[str, list[str]] = {}
    for name, path in sorted(closure.items()):
        reached = sorted(n for n in _all_imported_names(path) if _reaches_io(n))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
            for node in ast.walk(tree)
        ):
            reached.append("open()")
        if reached:
            offenders[name] = reached

    assert not offenders, (
        "bib_merge's closure can perform I/O:\n"
        + "\n".join(f"  {m} → {hits}" for m, hits in sorted(offenders.items()))
    )


# ---------------------------------------------------------------------------
# Guard the guard: the extractor above is what every check on this page trusts,
# so an edge form it cannot see disables all of them silently.
# ---------------------------------------------------------------------------


def test_extractor_sees_package_level_import(tmp_path: Path) -> None:
    """``from pzi import http_api`` is an edge, not an opaque name.

    This form was invisible for 22 import sites: it parses with ``level == 0``
    and ``module == "pzi"``, which fails the ``startswith("pzi.")`` test, so the
    edge was dropped and every tier check below waved it through. All 22 were
    legal, which is exactly why nothing failed.
    """
    module = tmp_path / "sample.py"
    module.write_text("from pzi import http_api, exit_codes\n", encoding="utf-8")
    # `__init__` too: importing any name from `pzi` executes `pzi/__init__.py`,
    # so the package root is a real edge whether or not the name is a module.
    # Recording only the names meant `from pzi import cli_version_text` — a
    # re-exported function — produced no edge at all, and a cycle back through
    # the package root was invisible.
    assert _imported_pzi_modules(module) == {"http_api", "exit_codes", "__init__"}


def test_the_extractor_ignores_imports_inside_functions(tmp_path: Path) -> None:
    """A deferred import is not a load-time edge, and treating it as one lies.

    An import inside a function runs on call, so it cannot take part in an
    import cycle — which is why several modules use one deliberately to break
    one. Counting them reported cycles that cannot happen and would have forced
    a real fix to be reverted.
    """
    module = tmp_path / "sample.py"
    module.write_text(
        "from pzi import exit_codes\n"
        "\n"
        "def later():\n"
        "    from pzi import http_api\n"
        "    return http_api\n",
        encoding="utf-8",
    )
    assert _imported_pzi_modules(module) == {"exit_codes", "__init__"}


def test_graph_contains_package_level_edges() -> None:
    """The real graph carries edges only ever written as ``from pzi import X``.

    ``commands/init.py:10`` is the case with no absolute-form twin elsewhere in
    the file, so it is zero if the extractor regresses.
    """
    graph = _build_import_graph()
    assert "setup_service" in graph["commands.init"]
    assert "exit_codes" in graph["errors"]


def test_graph_drops_reexported_non_modules() -> None:
    """``from pzi import cli_version_text`` names a function, not a module.

    Left in, it would seed the graph with phantom nodes that no tier claims and
    ``test_all_modules_classified`` never sees.
    """
    graph = _build_import_graph()
    assert "cli_version_text" not in graph["ts_backend"]
    assert "cli_version_text" not in graph


def test_a_relative_import_resolves_to_the_module_it_names(tmp_path: Path) -> None:
    """`from .common import x` inside `pzi/commands/` means `commands.common`.

    It resolved to the bare stem `common`, which matches no module, so
    `_build_import_graph` dropped the edge — a back-edge written with a relative
    import was invisible to every check in this file.
    """
    module = _SRC / "commands" / "_probe_relative.py"
    module.write_text(
        "from .common import resolve_target\n"
        "from . import add\n"
        "from ..cli_render import render_cell\n",
        encoding="utf-8",
    )
    try:
        assert _imported_pzi_modules(module) == {
            "commands.common", "commands.add", "cli_render",
        }
    finally:
        module.unlink()


def test_the_extractor_ignores_a_type_checking_block(tmp_path: Path) -> None:
    """`if TYPE_CHECKING:` is module level but never executes.

    Keeping it made the lazily-bound public API look like an eager one and
    reported cycles that cannot happen at runtime — which would have argued for
    reverting the fix that removed the real ones.
    """
    module = tmp_path / "sample.py"
    module.write_text(
        "from typing import TYPE_CHECKING\n"
        "\n"
        "from pzi import exit_codes\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from pzi.http_api import serve\n",
        encoding="utf-8",
    )
    assert _imported_pzi_modules(module) == {"exit_codes", "__init__"}
