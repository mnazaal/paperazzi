"""pzi — Capture papers into local BibTeX libraries from DOI, URL, or PDF.

Public API exports — pure functions and types suitable for external consumers.
I/O entry points live in their respective modules and are not re-exported here.
"""

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

from pzi.bib_repository import (
    WritePlan,
    merge_entries,
    parse_bibtex,
    plan_bib_write,
    serialize_bibtex,
)
from pzi.bibtex import BibtexEntry, NormalizedRecord
from pzi.http_security import HttpSecurityConfig, build_http_security_config
from pzi.url_safety import safe_public_http_url


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
    "BibtexEntry",
    "HttpSecurityConfig",
    "NormalizedRecord",
    "WritePlan",
    "build_http_security_config",
    "cli_version_text",
    "merge_entries",
    "package_version",
    "parse_bibtex",
    "plan_bib_write",
    "safe_public_http_url",
    "serialize_bibtex",
]
