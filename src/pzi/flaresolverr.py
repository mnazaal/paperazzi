"""FlareSolverr client — fetches Cloudflare-protected pages via headless Chrome."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeAlias
from urllib.request import Request, urlopen

FetchText = Callable[[str, object], str]


FlareSolverrCookies: TypeAlias = dict[str, Any]



FlareSolverrSolution: TypeAlias = dict[str, Any]



def fetch_html_via_flaresolverr(
    url: str,
    *,
    server_url: str,
    post_json: FetchText | None = None,
) -> str | None:
    """Return page HTML fetched via FlareSolverr, or None on failure."""
    fn = post_json or _post_json
    try:
        endpoint = server_url.rstrip("/") + "/v1"
        raw = fn(endpoint, {"cmd": "request.get", "url": url, "maxTimeout": 60000})
        data = json.loads(raw)
        if data.get("status") != "ok":
            return None
        return data.get("solution", {}).get("response") or None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def fetch_pdf_via_flaresolverr(
    url: str,
    *,
    server_url: str,
    post_json: FetchText | None = None,
) -> bytes | None:
    """Return PDF bytes fetched via FlareSolverr, or None on failure.

    Uses FlareSolverr to bypass Cloudflare, then downloads the PDF
    using the obtained cookies.
    """
    fn = post_json or _post_json
    try:
        endpoint = server_url.rstrip("/") + "/v1"

        # Step 1: Get cookies by visiting the PDF URL
        raw = fn(endpoint, {"cmd": "request.get", "url": url, "maxTimeout": 60000})
        data = json.loads(raw)
        if data.get("status") != "ok":
            return None

        solution = data.get("solution", {})
        cookies = solution.get("cookies", [])
        user_agent = solution.get("userAgent", "Mozilla/5.0")

        # Step 2: Download PDF using the cookies
        return _download_with_cookies(url, cookies, user_agent)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _download_with_cookies(
    url: str,
    cookies: list[FlareSolverrCookies],
    user_agent: str,
) -> bytes | None:
    """Download a file using cookies obtained from FlareSolverr."""
    from http.cookiejar import Cookie, CookieJar
    from urllib.parse import urlparse

    jar = CookieJar()
    parsed = urlparse(url)
    domain = parsed.hostname or ""

    for cookie in cookies:
        c = Cookie(
            version=0,
            name=cookie["name"],
            value=cookie["value"],
            port=None,
            port_specified=False,
            domain=cookie.get("domain", domain),
            domain_specified=bool(cookie.get("domain")),
            domain_initial_dot=cookie.get("domain", "").startswith("."),
            path=cookie.get("path", "/"),
            path_specified=True,
            secure=cookie.get("secure", False),
            expires=cookie.get("expiry"),
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": cookie.get("httpOnly", False)},
            rfc2109=False,
        )
        jar.set_cookie(c)


    # Use a custom opener with the cookie jar
    import urllib.request

    handler = urllib.request.HTTPCookieProcessor(jar)
    opener = urllib.request.build_opener(handler)

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/pdf,*/*",
        },
    )

    with opener.open(request, timeout=90) as response:
        data = response.read()  # pragma: no cover — covered by integration/browser tests
        if data.startswith(b"%PDF-"):  # pragma: no cover — covered by integration/browser tests
            return data  # pragma: no cover — covered by integration/browser tests
        return None  # pragma: no cover — covered by integration/browser tests


def _post_json(endpoint: str, payload: object) -> str:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
# pragma: no cover — covered by integration/browser tests
        return response.read().decode("utf-8")  # pragma: no cover
