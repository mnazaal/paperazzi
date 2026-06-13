"""Helper factories for paywall scenario tests.

Each factory reads real-format fixture JSON and returns a lambda matching
the injected function signature used by add_input_to_bib() and related code.

IMPORTANT: The fetch_web / fetch_search injection points replace
fetch_web_translations() and fetch_search_translations(), which already
normalize raw translator output via normalize_translation_item().
Therefore the mock must return the *normalized* shape:
    [{"item_type": ..., "record": {...}, "attachments": [...]}]
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pzi.translation_server import normalize_translation_item

PAYWALL_DIR = Path(__file__).parent / "fixtures" / "paywall"


def _load_json(name: str) -> Any:
    return json.loads((PAYWALL_DIR / name).read_text())


def make_fetch_web_from_article_fixture() -> Callable[..., list[dict[str, Any]]]:
    """Return fetch_web(url, server_url, **kw) → normalized translator results."""
    raw_items = _load_json("translation_response_article.json")
    fixture = [
        normalize_translation_item(item, source_url=item.get("url"))
        for item in raw_items
    ]
    return lambda url, server_url=None, **kw: fixture


def make_fetch_search_from_search_fixture() -> Callable[..., list[dict[str, Any]]]:
    """Return fetch_search(query, server_url, **kw) → normalized search results."""
    raw_items = _load_json("translation_response_search.json")
    fixture = [normalize_translation_item(item) for item in raw_items]
    return lambda query, server_url=None, **kw: fixture


def make_fetch_crossref_from_fixture() -> Callable[..., dict[str, Any] | None]:
    """Return fetch_crossref(doi, *, contact_email) → real Crossref JSON.

    Returns the message portion (which is what metadata_sources expects at the
    top level when calling _crossref_normalize_work).
    """
    fixture = _load_json("crossref_response.json")
    return lambda doi, *, contact_email=None: fixture


def make_fetch_unpaywall_from_fixture() -> Callable[..., str | None]:
    """Return fetch_unpaywall(doi, *, email) → PDF URL from Unpaywall fixture."""
    _fixture = _load_json("unpaywall_response_oa.json")
    return lambda doi, *, email=None: "https://arxiv.org/pdf/2503.12345.pdf"


def make_fetch_binary_403(url: str) -> tuple[bytes, str]:
    """Simulate direct fetch blocked by publisher (HTTP 403)."""
    from urllib.error import HTTPError
    raise HTTPError(url, 403, "Forbidden", {}, None)


def make_fetch_binary_returns_pdf(*, content: bytes = b"%PDF-1.4 mock payload\n") -> Callable[..., tuple[bytes, str]]:
    """Return fetch_binary(url) → PDF bytes."""
    return lambda url: (content, "application/pdf")


def make_fetch_binary_selective(
    *,
    blocked_hosts: list[str] | None = None,
    pdf_content: bytes = b"%PDF-1.4 mock payload\n",
) -> Callable[..., tuple[bytes, str]]:
    """Return fetch_binary that blocks certain hosts (403) but returns PDF for others."""
    blocked = blocked_hosts or ["jmlr.org", "publisher.com"]

    def _fetch(url: str) -> tuple[bytes, str]:
        from urllib.error import HTTPError
        for host in blocked:
            if host in url:
                raise HTTPError(url, 403, "Forbidden", {}, None)
        return (pdf_content, "application/pdf")

    return _fetch
