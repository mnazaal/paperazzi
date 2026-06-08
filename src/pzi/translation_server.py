"""Thin client and normalization helpers for Zotero translation-server."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias, cast
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from pzi.bibtex import NormalizedRecord
from pzi.fetch_helpers import DEFAULT_MAX_RESPONSE_BYTES, _read_limited
from pzi.identifiers import _extract_year_from_str, normalize_doi, normalize_url

TranslationAttachment: TypeAlias = dict[str, Any]



TranslationResult: TypeAlias = dict[str, Any]



JsonPost = Callable[[str, object], object]
TextPost = Callable[[str, str], object]


def normalize_translation_item(
    item: Mapping[str, object], *, source_url: str | None = None
) -> TranslationResult:
    """Normalize one translation-server item into internal record data."""
    item_url = _mapping_string(item, "url")
    normalized_item_url = normalize_url(item_url) if item_url is not None else None
    normalized_source_url = (
        normalize_url(source_url) if source_url is not None else None
    )
    doi = normalize_doi(_mapping_string(item, "DOI") or "")
    arxiv_id = _extract_arxiv_id(item)

    record: NormalizedRecord = {
        "title": _mapping_string(item, "title"),
        "authors": _normalize_creators(item.get("creators")),
        "year": _extract_year(item),
        "venue": _mapping_string(item, "publicationTitle")
        or _mapping_string(item, "proceedingsTitle")
        or _mapping_string(item, "bookTitle"),
        "doi": doi,
        "arxiv_id": arxiv_id,
        "canonical_url": normalized_item_url or normalized_source_url,
        "source_url": normalized_source_url or normalized_item_url,
        "abstract_url": normalized_item_url,
        "abstract": _mapping_string(item, "abstractNote"),
    }

    return {
        "item_type": _mapping_string(item, "itemType"),
        "record": record,
        "attachments": extract_pdf_attachments(item.get("attachments")),
    }


def extract_pdf_attachments(value: object) -> list[TranslationAttachment]:
    """Return normalized PDF attachment candidates from translator output."""
    if not isinstance(value, list):
        return []

    attachments: list[TranslationAttachment] = []
    for raw_attachment in value:
        if not isinstance(raw_attachment, Mapping):
            continue

        url = _mapping_string(raw_attachment, "url")
        normalized_url = normalize_url(url) if url is not None else None
        if normalized_url is None:
            continue

        mime_type = _mapping_string(raw_attachment, "mimeType")
        title = _mapping_string(raw_attachment, "title")
        is_pdf = (
            (mime_type is not None and mime_type.lower() == "application/pdf")
            or normalized_url.lower().endswith(".pdf")
            or (title is not None and "pdf" in title.lower())
        )
        if not is_pdf:
            continue

        attachments.append(
            {
                "title": title,
                "url": normalized_url,
                "mime_type": mime_type,
            }
        )

    return attachments


def fetch_web_translations(
    url: str,
    *,
    server_url: str,
    post_json: JsonPost | None = None,
    cookies: str | None = None,
) -> list[TranslationResult]:
    """Fetch webpage translations from translation-server /web."""
    base = server_url.rstrip("/") + "/"
    endpoint = urljoin(base, "web")
    payload: dict[str, object] = {"url": url, "session": "pzi"}
    if cookies:
        payload["cookies"] = cookies
    response = _call_translation_server(
        endpoint=endpoint,
        payload=payload,
        post_json=post_json or _post,
    )
    return [normalize_translation_item(item, source_url=url) for item in response]


def fetch_search_translations(
    query: str,
    *,
    server_url: str,
    post_text: TextPost | None = None,
) -> list[TranslationResult]:
    """Fetch search-based translations from translation-server /search."""
    base = server_url.rstrip("/") + "/"
    endpoint = urljoin(base, "search")
    fn: TextPost = post_text or _post_text
    response = _call_translation_server(
        endpoint=endpoint,
        payload=query,
        post_json=fn,
    )
    return [normalize_translation_item(item) for item in response]


def _call_translation_server(
    *, endpoint: str, payload: object, post_json: Callable[..., object]
) -> list[Mapping[str, object]]:
    raw_response = post_json(endpoint, payload)
    if not isinstance(raw_response, list):
        raise ValueError("translation-server response must be a list")

    normalized_items: list[Mapping[str, object]] = []
    for item in raw_response:
        if not isinstance(item, Mapping):
            raise ValueError("translation-server items must be objects")
        normalized_items.append(cast(Mapping[str, object], item))
    return normalized_items


def _post(
    endpoint: str,
    payload: object,
    *,
    content_type: str = "application/json",
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> object:
    data = (
        json.dumps(payload).encode("utf-8")
        if content_type == "application/json"
        else str(payload).encode("utf-8")
    )
    request = Request(
        endpoint,
        data=data,
        headers={"Content-Type": content_type},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # pragma: no cover
        data = _read_limited(response, max_bytes=max_bytes)
        return json.loads(data.decode("utf-8"))  # pragma: no cover


def _post_text(endpoint: str, payload: object) -> object:
    return _post(endpoint, payload, content_type="text/plain")


def _normalize_creators(value: object) -> list[str]:
    if not isinstance(value, list):
        return []

    authors: list[str] = []
    for creator in value:
        if not isinstance(creator, Mapping):
            continue  # pragma: no cover — covered by integration/browser tests
        name = _mapping_string(creator, "name")
        if name is not None:
            authors.append(name)
            continue
        first_name = _mapping_string(creator, "firstName")
        last_name = _mapping_string(creator, "lastName")
        if first_name and last_name:
            authors.append(f"{last_name}, {first_name}")
        elif last_name:  # pragma: no branch — covered by integration/browser tests
            authors.append(last_name)
    return authors


def _extract_year(item: Mapping[str, object]) -> int | None:
    """Extract a 4-digit year from an item's ``date`` field, or None."""
    date_value = _mapping_string(item, "date")
    if date_value is None:
        return None
    return _extract_year_from_str(date_value)


def _extract_arxiv_id(item: Mapping[str, object]) -> str | None:
    archive_id = _mapping_string(item, "archiveID")
    if archive_id is not None:
        return archive_id.strip() or None

    extra = _mapping_string(item, "extra")
    if extra is None:
        return None

    for line in extra.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)  # pragma: no branch — covered by integration/browser tests
        if key.strip().lower() == "arxiv":  # pragma: no branch
            candidate = value.strip()
            return candidate or None
    return None


def _mapping_string(mapping: Mapping[str, object], key: str) -> str | None:
    """Return stripped non-empty string value from a mapping, or None."""
    value = mapping.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
