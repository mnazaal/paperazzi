"""Optional external headless-browser PDF discovery and download hook."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys


def _validate_browser_command(command: str) -> list[str]:
    """Split and validate a browser PDF hook command, raising on unsafe input."""
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("empty browser command in config")
    return tokens


# Control characters (U+0000-U+001F) — stripped from subprocess stderr
# before printing to prevent terminal escape injection.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _safe_stderr(text: str) -> str:
    """Strip terminal control characters from subprocess stderr output."""
    return _CONTROL_RE.sub("", text)


def discover_pdf_url_with_browser(
    *, command: str, page_url: str, doi: str | None = None
) -> str | None:
    """Discover PDF URL from a page using external browser hook."""
    payload = json.dumps({"page_url": page_url, "doi": doi})
    try:
        tokens = _validate_browser_command(command)
        result = subprocess.run(
            tokens,
            input=payload,
            shell=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
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
    if not isinstance(pdf_url, str):
        return None
    pdf_url = pdf_url.strip()
    return pdf_url if pdf_url else None


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
    try:
        tokens = _validate_browser_command(command)
        result = subprocess.run(
            tokens,
            input=payload,
            shell=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print(
            "browser PDF hook timed out while trying to download PDF",
            file=sys.stderr,
        )
        return None
    child_stderr = getattr(result, "stderr", "")
    if result.returncode != 0:
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
    stdout = result.stdout.strip()
    if not stdout:
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
    pdf_base64 = data.get("pdf_base64")
    if not isinstance(pdf_base64, str):
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
    import base64
    try:
        pdf_bytes = base64.b64decode(pdf_base64, validate=True)
        if pdf_bytes.startswith(b"%PDF-"):
            return pdf_bytes
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
    except (ValueError, TypeError):
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return None
