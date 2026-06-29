"""Architectural guard: the pure/planning layer must not import front-ends.

pzi keeps pure planning logic separate from the side-effecting front-ends (CLI,
HTTP API, browser hooks). That split is otherwise only a convention enforced by
review; this test makes it mechanical.

Every pzi/*.py module is classified into exactly one of five tiers:

  STRICT_PURE  — no front-end AND no browser imports, even transitively.
  PIPELINE     — may reach browser hooks (PDF/discovery), never front-end.
  SERVICE      — service-layer modules; no *direct* front-end imports.
  FRONTEND     — CLI, commands, HTTP API layers.
  BROWSER      — browser/server-browser hook modules.

Any module not in any tier fails test_all_modules_classified(), forcing an
explicit decision when a new file is added (no silent drift).

The guard checks:
  • Relative imports (``from . import x``) are resolved within pzi, so a
    back-edge via a relative import is caught the same as an absolute one.
  • STRICT_PURE: transitive closure must not reach FRONTEND or BROWSER.
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

STRICT_PURE: frozenset[str] = frozenset(
    {
        # Core data / serialization / algorithm
        "bibtex",
        "similarity",
        "url_safety",
        "capture_models",
        "pdf_planning",
        "protocols",
        "bib_serialize",
        "format_templates",
        "identifiers",
        "resolution_match",
        # Config / error / IO primitives
        "config",
        "errors",
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
        "cli",
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


def _imported_pzi_modules(path: Path) -> set[str]:
    """Return pzi-internal module stems imported directly by *path*.

    Handles both absolute (``from pzi.foo import …``) and relative
    (``from . import foo`` / ``from .foo import …``) forms so a back-edge
    introduced via a relative import is caught the same as an absolute one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pzi."):
                    names.add(alias.name.removeprefix("pzi."))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module.startswith("pzi."):
                    names.add(node.module.removeprefix("pzi."))
            elif node.level > 0:
                # Relative import inside the pzi package.
                if node.module:
                    # ``from .foo import bar`` — the module stem is foo.
                    names.add(node.module)
                else:
                    # ``from . import bar, baz`` — the names *are* the stems.
                    for alias in node.names:
                        names.add(alias.name)
    return names


def _build_import_graph() -> dict[str, set[str]]:
    """Parse every pzi/*.py module and return stem → {directly-imported stems}."""
    graph: dict[str, set[str]] = {}
    for path in sorted(_SRC.glob("*.py")):
        if path.stem in ("__init__", "__main__"):
            continue
        graph[path.stem] = _imported_pzi_modules(path)
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
    all_modules = {
        p.stem
        for p in _SRC.glob("*.py")
        if p.stem not in ("__init__", "__main__")
    }
    all_tiers = STRICT_PURE | PIPELINE | SERVICE | FRONTEND | BROWSER

    unclassified = all_modules - all_tiers
    assert not unclassified, (
        f"unclassified pzi modules (add to one tier in test_layer_boundaries.py): "
        f"{sorted(unclassified)}"
    )

    # Also assert no module appears in more than one tier (tiers are disjoint).
    tier_list = [STRICT_PURE, PIPELINE, SERVICE, FRONTEND, BROWSER]
    for i, tier_a in enumerate(tier_list):
        for tier_b in tier_list[i + 1 :]:
            overlap = tier_a & tier_b
            assert not overlap, f"module appears in multiple tiers: {sorted(overlap)}"


def test_strict_pure_no_frontend_or_browser_transitively() -> None:
    """STRICT_PURE modules must not reach FRONTEND or BROWSER, even transitively.

    A single-hop back-edge (``capture_core`` → some helper → ``http_api``) is
    caught here where the old direct-only check would have missed it.
    """
    graph = _build_import_graph()
    forbidden = FRONTEND | BROWSER
    offenders: dict[str, list[str]] = {}
    for mod in STRICT_PURE:
        reached = _transitive_deps(mod, graph) & forbidden
        if reached:
            offenders[mod] = sorted(reached)
    assert not offenders, (
        "STRICT_PURE modules transitively import FRONTEND or BROWSER:\n"
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
