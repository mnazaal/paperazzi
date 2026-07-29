"""pzi — Capture papers into local BibTeX libraries from DOI, URL, or PDF.

This package is a CLI and a local HTTP API; it is not designed as a library.
Only the version helpers are re-exported here. Import anything else from the
module that defines it (``pzi.bib_repository``, ``pzi.bibtex``, …), which is
what the codebase itself does.
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

__all__ = [
    "cli_version_text",
    "package_version",
]
