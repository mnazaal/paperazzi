"""pzi — Capture papers into local BibTeX libraries from DOI, URL, or PDF.

A CLI, a local HTTP API, and a small Python API. **``__all__`` is the whole of
the public surface** and is frozen: parameter names, defaults and return shapes
are a compatibility promise, checked by ``tests/test_public_api.py``.

Everything else is internal, whatever its docstring says. The modules behind
this (``pzi.bib_repository``, ``pzi.add_service``, …) are shaped for the CLI and
for testing — several take injected fetcher parameters that exist so tests can
replace the network — and they may change in any release. Import from them at
your own risk; ``pzi.api`` is the supported way in.

    import pzi
    for match in pzi.search(query="dimensional collapse"):
        print(match["citekey"])
"""

import logging
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

# bibtexparser logs `Unknown block type <class '...DuplicateBlockKeyBlock'>` at
# WARNING for every failed block its middlewares walk past. pzi configures no
# logging, so Python's `lastResort` handler printed that bare to stderr during
# an ordinary `pzi entries` run. Attaching a handler stops `lastResort` firing
# without discarding anything we rely on: failed blocks are read straight off
# `library.failed_blocks` and reported in our own words by
# `bib_serialize.describe_failed_blocks`.
logging.getLogger("bibtexparser").addHandler(logging.NullHandler())


def package_version(
    distribution_name: str = "paperazzi",
    *,
    lookup_version: Callable[[str], str] = metadata_version,
) -> str:
    """Return installed package version, or stable fallback for source-tree use."""
    try:
        return lookup_version(distribution_name)
    except PackageNotFoundError:
        return "unknown"


def cli_version_text(
    package_name: str = "pzi",
    *,
    distribution_name: str = "paperazzi",
    version_text: str | None = None,
) -> str:
    """Return argparse-compatible CLI version string.

    ``package_name`` is the printed label (the ``pzi`` command); ``distribution_name``
    is the installed PyPI distribution (``paperazzi``) used to look up the version —
    these differ because the distribution name and CLI command name are not the same.
    """
    resolved_version = (
        package_version(distribution_name) if version_text is None else version_text
    )
    return f"{package_name} {resolved_version}"


__version__ = package_version()

# Imported at the bottom: `pzi.api` imports the service modules, which import
# back from `pzi` (`package_version` reaches `http_get_routes`), so binding the
# version helpers first is what keeps that cycle resolvable.
from pzi.api import (  # noqa: E402
    add,
    check,
    dedupe,
    entries,
    export,
    promote,
    search,
)

__all__ = [
    "add",
    "check",
    "cli_version_text",
    "dedupe",
    "entries",
    "export",
    "package_version",
    "promote",
    "search",
]
