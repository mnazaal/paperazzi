"""Shared HTTP fetch utilities used across API client modules."""

from __future__ import annotations

import time
import urllib.error
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = "pzi/1.0 (mailto:pzi)"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2


def fetch_text(
    url: str,
    *,
    api_key: str | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
) -> str:
    """Fetch a URL and return the response body decoded as UTF-8 text.

    Retries on transient network errors (URLError, TimeoutError) with
    exponential backoff.  Does NOT retry on HTTPError (4xx/5xx status).
    """
    headers: dict[str, str] = {"User-Agent": user_agent}
    if api_key:
        headers["x-api-key"] = api_key

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = Request(url, headers=headers, method="GET")
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError:
            raise  # 4xx/5xx — do not retry
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(min(2**attempt, 8))  # 0s, 2s, 4s (capped at 8)

    raise last_error  # type: ignore[misc]


def fetch_binary(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRIES,
) -> tuple[bytes, str | None]:
    """Fetch a URL and return (raw_bytes, content_type).

    Retries on transient network errors with exponential backoff.
    Does NOT retry on HTTPError (4xx/5xx status).
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type")
                return response.read(), content_type
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(min(2**attempt, 8))  # pragma: no cover

    raise last_error  # type: ignore[misc]
