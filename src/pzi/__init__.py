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
from typing import TYPE_CHECKING

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

# The public API is bound *lazily*, and that is structural rather than a
# micro-optimisation. Importing `pzi.api` here creates a real edge from the
# package root to every service module — while dozens of leaf modules do
# `from pzi import exit_codes`, which executes this file. Eagerly, that is a
# genuine cycle (`pzi -> api -> capture_core -> add_service -> pzi`, and a dozen
# more), resolved at runtime only by the accident that the version helpers above
# are bound before the import ran. It also put argparse, http.client and the
# whole capture stack in the closure of a bare `import pzi`, so a script calling
# only `pzi.search()` paid for all of it.
#
# PEP 562: `pzi.search` resolves on first attribute access, and
# `from pzi import search` works unchanged.
# Statically visible, never imported at runtime. `py.typed` ships, so the whole
# point of these annotations is that a downstream type checker resolves them —
# and a name bound only through `__getattr__` is invisible to one. This block
# gives the checker the real symbols while the runtime keeps the lazy binding.
if TYPE_CHECKING:
    from pzi.api import (
        add,
        check,
        dedupe,
        entries,
        export,
        promote,
        search,
    )
    from pzi.errors import PziError

_PUBLIC_API = frozenset(
    {"add", "check", "dedupe", "entries", "export", "promote", "search"}
)


def __getattr__(name: str) -> object:
    if name in _PUBLIC_API:
        from pzi import api

        return getattr(api, name)
    if name == "PziError":
        from pzi.errors import PziError

        return PziError
    raise AttributeError(f"module 'pzi' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})


__all__ = [
    "PziError",
    "__version__",
    "add",
    "check",
    "dedupe",
    "entries",
    "export",
    "promote",
    "search",
]
