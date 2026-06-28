"""Pure capture request models and artifact loaders.

No network, no BibTeX writes.  CLI, HTTP, and browser-extension adapters can
all build these shapes before calling service code.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pzi.fileio import read_text_utf8

_SOURCE_PRIORITY: dict[str, int] = {
    "cli": 10,
    "translation": 9,
    "browser": 8,
    "page": 7,
    "server": 6,
    "observed": 5,
    "network_observed": 5,
    "site_module": 4,
    "dom": 3,
    "active_tab": 2,
    "http": 1,
}


@dataclass(frozen=True)
class PdfCandidate:
    """One PDF candidate discovered by CLI flags, page metadata, or browser UI."""

    value: str
    source: str = "unknown"
    kind: str = "url"
    confidence: int = 50
    requires_cookies: bool = False
    requires_permission: bool = False


@dataclass(frozen=True)
class PageArtifact:
    """Optional saved page artifact for later metadata extraction or user tooling."""

    html: str
    source: str
    path: str | None = None


@dataclass(frozen=True)
class AuthHints:
    """Auth/session hints captured at boundary, e.g. browser Cookie header."""

    cookies: str | None = None


@dataclass(frozen=True)
class CaptureInput:
    """Pure capture request shared by CLI, HTTP, and browser adapters."""

    value: str
    record_overrides: dict[str, object] = field(default_factory=dict)
    bib_selector: str | None = None
    pdf_candidates: tuple[PdfCandidate, ...] = field(default_factory=tuple)
    page_artifact: PageArtifact | None = None
    auth_hints: AuthHints = field(default_factory=AuthHints)


@dataclass(frozen=True)
class CaptureOptions:
    """Side-effect-free capture options shared by CLI and HTTP adapters."""

    dry_run: bool = False
    force_new: bool = False
    page_metadata_cmd: str | None = None
    page_metadata_timeout_seconds: int = 5


def _source_rank(source: str) -> int:
    return _SOURCE_PRIORITY.get(source, 0)


def normalize_pdf_candidates(
    candidates: Iterable[PdfCandidate],
) -> tuple[PdfCandidate, ...]:
    """Deduplicate PDF candidates by value, keeping highest confidence on ties."""
    seen: dict[str, PdfCandidate] = {}
    for c in candidates:
        existing = seen.get(c.value)
        if existing is None or c.confidence > existing.confidence:
            seen[c.value] = c
    return tuple(seen.values())


def rank_pdf_candidates(
    candidates: Iterable[PdfCandidate],
) -> tuple[PdfCandidate, ...]:
    """Sort PDF candidates: paths before URLs, then source priority, then confidence desc."""
    return tuple(
        sorted(
            candidates,
            key=lambda c: (
                0 if c.kind == "path" else 1,
                -_source_rank(c.source),
                -c.confidence,
            ),
        )
    )


def load_page_artifact(path: str, *, stdin_text: str | None = None) -> PageArtifact:
    """Load page HTML from a path or '-' stdin marker."""
    if path == "-":
        if stdin_text is None:
            import sys

            html = sys.stdin.read()
        else:
            html = stdin_text
        return PageArtifact(html=html, source="stdin", path=None)

    html = read_text_utf8(path)
    return PageArtifact(html=html, source="file", path=path)
