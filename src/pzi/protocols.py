"""Typed contracts for the dependency-injected fetcher seams.

The capture pipeline injects metadata/PDF fetchers into ``add_service``,
``add_planning``, ``capture_local_pdf`` and ``pdf_discovery`` so the network
layer can be swapped out (e.g. by tests).  Those injection points would
otherwise carry an implicit ``Any``; the ``Protocol``s below give them a static
contract instead, so a fetcher whose signature drifts is caught by the type
checker at the call site.

Keyword-argument seams use ``Protocol`` (``Callable[...]`` cannot express
keyword-only parameters); single-positional-argument seams reuse plain
``Callable`` aliases, matching the existing ``FetchText`` / ``FetchBinary``
aliases in :mod:`pzi.pdf` and :mod:`pzi.metadata_sources`.

Concrete implementations live in :mod:`pzi.translation_server`,
:mod:`pzi.metadata_sources`, :mod:`pzi.pdf`, :mod:`pzi.fetch_helpers`, and
:mod:`pzi.flaresolverr`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Protocol

from pzi.bibtex import NormalizedRecord
from pzi.translation_server import TranslationResult

# The leading value (url/query/doi/title) is positional-only (``/``): every
# call site passes it positionally, and the concrete implementations name it
# differently (e.g. ``fetch_crossref_record(doi=...)`` vs
# ``fetch_crossref_record_by_title(title=...)``), so the name must not be part
# of the contract.


class WebTranslationFetcher(Protocol):
    """Zotero translation-server ``/web`` fetcher (URL → translation results)."""

    def __call__(
        self, url: str, /, *, server_url: str, cookies: str | None = ...
    ) -> list[TranslationResult]: ...


class SearchTranslationFetcher(Protocol):
    """Zotero translation-server ``/search`` fetcher (query → translation results)."""

    def __call__(self, query: str, /, *, server_url: str) -> list[TranslationResult]: ...


class UnpaywallFetcher(Protocol):
    """Unpaywall open-access PDF lookup (DOI + email → PDF URL)."""

    def __call__(self, doi: str, /, *, email: str) -> str | None: ...


class MetadataRecordFetcher(Protocol):
    """Crossref/OpenAlex-style identifier → normalized record fetcher.

    Used for both DOI lookups and title searches; the positional argument is the
    DOI or the title depending on the concrete implementation.
    """

    def __call__(
        self,
        value: str,
        /,
        *,
        contact_email: str | None = ...,
        errors: list[str] | None = ...,
    ) -> NormalizedRecord | None: ...


# Single-positional-argument seams — a plain ``Callable`` alias is enough.
S2RecordFetcher = Callable[[str], NormalizedRecord | None]
# Promotion's Semantic Scholar seam reports the provider error alongside the
# record so rate-limit/auth failures can be surfaced to the user.
S2RecordWithErrorFetcher = Callable[[str], tuple[NormalizedRecord | None, str | None]]
HtmlFetcher = Callable[[str], str | None]
BinaryFetcher = Callable[[str], tuple[bytes, str | None]]


def accepts_keyword(fn: object, name: str) -> bool:
    """True when *fn* accepts a keyword argument called *name*.

    Used to decide whether an injected seam takes an optional extra (a contact
    email, a user agent) instead of calling it and catching ``TypeError``. The
    try/except form could not tell "this fetcher has a narrower signature" from
    "this fetcher raised TypeError internally", so a real bug inside a provider
    became a silent retry with fewer arguments and a plausible-looking result.
    """
    try:
        parameters = inspect.signature(fn).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # Un-introspectable callable (some builtins/C extensions): assume the
        # narrow form, which every seam here supports.
        return False
    if name in parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
