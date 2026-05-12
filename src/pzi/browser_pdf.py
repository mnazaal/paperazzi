"""Optional external headless-browser PDF discovery and download hook."""

from __future__ import annotations

import json
import shlex
import subprocess


def discover_pdf_url_with_browser(
    *, command: str, page_url: str, doi: str | None = None
) -> str | None:
    """Discover PDF URL from a page using external browser hook."""
    payload = json.dumps({"page_url": page_url, "doi": doi})
    result = subprocess.run(
        shlex.split(command),
        input=payload,
        shell=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout if stdout.startswith(("http://", "https://")) else None
    if not isinstance(data, dict):
        return None
    pdf_url = data.get("pdf_url")
    return pdf_url if isinstance(pdf_url, str) and pdf_url.strip() else None


def download_pdf_with_browser(
    *, command: str, pdf_url: str
) -> bytes | None:
    """Download PDF bytes using external browser hook.

    Sends JSON on stdin: {"action": "download_pdf", "pdf_url": "..."}
    Expects base64-encoded PDF on stdout: {"pdf_base64": "..."}

    The command should include browser profile path for authenticated access:
      python /path/to/browser_pdf_hook.py --profile ~/.mozilla/firefox/xxx.default
      python /path/to/browser_pdf_hook.py --profile ~/.config/google-chrome/Default --browser chrome
    """
    payload = json.dumps({"action": "download_pdf", "pdf_url": pdf_url})
    result = subprocess.run(
        shlex.split(command),
        input=payload,
        shell=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pdf_base64 = data.get("pdf_base64")
    if not isinstance(pdf_base64, str):
        return None
    import base64
    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        if pdf_bytes.startswith(b"%PDF-"):
            return pdf_bytes
        return None
    except (ValueError, TypeError):
        return None
