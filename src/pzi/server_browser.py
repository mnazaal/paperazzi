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
        headers["X-Pzi-Token"] = auth_token
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
    errors: list[str] | None = None,
) -> bytes | None:
    """Download PDF bytes via the server's /browser/download endpoint.

    *errors* collects why this stage produced nothing. Without it every outcome
    was `None`, and the caller reported all of them as "server browser: no PDF
    returned" — which asserts a server answered. `pdf_service` synthesizes an
    `api_url` from the listen host/port whenever config has none, so this stage
    runs on every machine whether or not `pzi server` is up, and the commonest
    reason for `None` is that nothing was listening on that port at all.
    """
    body = json.dumps({"pdf_url": pdf_url}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["X-Pzi-Token"] = auth_token
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
        if errors is not None:
            errors.append(f"{api_url}: response carried no PDF")
        return None
    except (HTTPError, json.JSONDecodeError, ValueError, TypeError) as exc:
        if errors is not None:
            errors.append(f"{api_url}: bad response ({exc})")
        return None
    except (URLError, OSError) as exc:
        if errors is not None:
            reason = getattr(exc, "reason", None) or exc
            errors.append(f"{api_url}: not reachable ({reason})")
        return None
