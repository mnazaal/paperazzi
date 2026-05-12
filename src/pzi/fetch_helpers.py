"""Shared HTTP fetch utilities used across API client modules."""

from __future__ import annotations

from urllib.request import Request, urlopen


def fetch_text(url: str, *, api_key: str | None = None) -> str:
    """Fetch a URL and return the response body decoded as UTF-8 text."""
    headers: dict[str, str] = {"User-Agent": "pzi/1.0 (mailto:pzi)"}
    if api_key:
        headers["x-api-key"] = api_key
    request = Request(url, headers=headers, method="GET")
    with urlopen(request) as response:
        return response.read().decode("utf-8")


def fetch_binary(url: str) -> tuple[bytes, str | None]:
    """Fetch a URL and return (raw_bytes, content_type)."""
    request = Request(url, method="GET")
    with urlopen(request) as response:
        content_type = response.headers.get("Content-Type")
        return response.read(), content_type
