"""FlareSolverr client — fetches Cloudflare-protected pages via headless Chrome."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeAlias
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from pzi.fetch_helpers import DEFAULT_MAX_RESPONSE_BYTES, read_limited

FetchText = Callable[[str, object], str]


def _valid_server_url(server_url: str) -> bool:
    """Return True for an http(s) FlareSolverr endpoint.

    Config already drops a malformed ``flaresolverr_url``; this is a
    defence-in-depth guard so the public fetchers never hand a non-http(s)
    scheme (e.g. ``file://``) to the raw ``urlopen`` in :func:`_post_json`.
    """
    parts = urlsplit(server_url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


FlareSolverrCookies: TypeAlias = dict[str, Any]






def _target_url_allowed(url: str) -> bool:
    """Whether *url* may be handed to FlareSolverr to fetch.

    FlareSolverr drives a real headless Chrome, and the URL was forwarded
    straight through — so the local browser was an open proxy for whatever a
    metadata provider or a captured page put in `pdf_url`. Reproduced against
    the cloud metadata endpoint: pzi asked FlareSolverr for
    `169.254.169.254/latest/meta-data/iam/security-credentials/`, which every
    other fetch path in pzi already refuses.

    Same predicate as the rest of pzi (`safe_public_http_url`), so a link-local,
    loopback or private destination is rejected here for the same reason it is
    everywhere else.
    """
    from pzi.url_safety import safe_public_http_url

    return safe_public_http_url(url)


def fetch_html_via_flaresolverr(
    url: str,
    *,
    server_url: str,
    post_json: FetchText | None = None,
) -> str | None:
    """Return page HTML fetched via FlareSolverr, or None on failure."""
    if not _valid_server_url(server_url) or not _target_url_allowed(url):
        return None
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
    if not _valid_server_url(server_url) or not _target_url_allowed(url):
        return None
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
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError, AttributeError):
        # The last three are defence in depth against a response shape this code
        # does not anticipate: this is a step in the PDF fallback chain, which
        # advances on a falsy return and aborts entirely on an exception.
        return None


def _download_with_cookies(
    url: str,
    cookies: list[FlareSolverrCookies],
    user_agent: str,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> bytes | None:
    """Download a file using cookies obtained from FlareSolverr."""
    from http.cookiejar import Cookie, CookieJar
    from urllib.parse import urlparse

    jar = CookieJar()
    parsed = urlparse(url)
    domain = parsed.hostname or ""

    for cookie in cookies:
        # FlareSolverr's response is third-party input. `name` and `value` were
        # the only fields read with `[]` — every other one already used `.get()`
        # with a default — so one cookie missing a key raised `KeyError` out of
        # the whole PDF fallback chain, skipping the desktop fallback that comes
        # after this step. A cookie we cannot read is one to skip, not to crash
        # on; the rest of the jar is still usable.
        if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
            continue
        # `.get("domain", domain)` returns None, not the default, when the key is
        # present and null — which a real FlareSolverr response does send.
        cookie_domain = cookie.get("domain") or ""
        c = Cookie(
            version=0,
            name=cookie["name"],
            value=cookie["value"],
            port=None,
            port_specified=False,
            domain=cookie_domain or domain,
            domain_specified=bool(cookie_domain),
            domain_initial_dot=cookie_domain.startswith("."),
            path=cookie.get("path") or "/",
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


    # Use the SSRF-pinned opener (redirect re-validation + connect-time IP
    # pinning) augmented with the FlareSolverr cookie jar, so even this
    # opt-in Cloudflare-bypass path cannot be steered at a private/internal host.
    import urllib.request

    from pzi.safe_http import build_safe_opener

    handler = urllib.request.HTTPCookieProcessor(jar)
    opener = build_safe_opener(extra_handlers=[handler])

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/pdf,*/*",
        },
    )

    with opener.open(request, timeout=90) as response:
        data = read_limited(
            response,
            max_bytes=max_bytes,
        )
        if data.startswith(b"%PDF-"):
            return data
        return None


def _post_json(
    endpoint: str,
    payload: object,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> str:
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        data = read_limited(response, max_bytes=max_bytes)
        return data.decode("utf-8")  # pragma: no cover
