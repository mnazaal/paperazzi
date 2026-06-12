"""HTTP client helpers for delegating browser PDF work to a running pzi server.

When a pzi CLI invocation detects that the HTTP API server is reachable
on the same machine, PDF discovery and download are routed through the
server's persistent browser session instead of launching a new subprocess
per PDF.  This is the CLI side of the server-side BrowserSessionManager.
"""

from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def server_is_reachable(api_url: str, *, timeout: float = 0.5) -> bool:
    """Return True if the pzi HTTP API server responds to /health."""
    try:
        url = f"{api_url.rstrip('/')}/health"
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (OSError, HTTPError, URLError, ValueError):
        return False


def discover_via_server_api(
    api_url: str,
    page_url: str,
    *,
    doi: str | None = None,
    auth_token: str | None = None,
    timeout: int = 120,
) -> str | None:
    """Discover PDF URL via the server's /browser/discover endpoint."""
    body = json.dumps({
        "page_url": page_url,
        **({"doi": doi} if doi else {}),
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["X-Pzi-Auth"] = auth_token
    try:
        url = f"{api_url.rstrip('/')}/browser/discover"
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict):
            pdf_url = data.get("pdf_url")
            if isinstance(pdf_url, str) and pdf_url.strip():
                return pdf_url.strip()
        return None
    except (OSError, HTTPError, URLError, json.JSONDecodeError, ValueError):
        return None


def download_via_server_api(
    api_url: str,
    pdf_url: str,
    *,
    auth_token: str | None = None,
    timeout: int = 180,
) -> bytes | None:
    """Download PDF bytes via the server's /browser/download endpoint."""
    body = json.dumps({"pdf_url": pdf_url}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["X-Pzi-Auth"] = auth_token
    try:
        url = f"{api_url.rstrip('/')}/browser/download"
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict):
            b64 = data.get("pdf_base64")
            if isinstance(b64, str) and b64:
                pdf_bytes = base64.b64decode(b64, validate=True)
                if pdf_bytes.startswith(b"%PDF-"):
                    return pdf_bytes
        return None
    except (OSError, HTTPError, URLError, json.JSONDecodeError, ValueError, TypeError):
        return None
