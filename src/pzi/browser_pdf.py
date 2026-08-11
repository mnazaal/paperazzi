"""Optional external headless-browser PDF discovery and download hook."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys


def _validate_browser_command(command: str) -> list[str]:
    """Split and validate a browser PDF hook command, raising on unsafe input.

    ``shell=False`` execution means the shell never expands ``~``, so do it
    here: this lets ``config.toml`` carry ``~/...`` paths (e.g. the interpreter
    or a browser ``--profile``) instead of absolute home paths. Tokens without a
    leading ``~`` are unchanged.
    """
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("empty browser command in config")
    return [os.path.expanduser(token) for token in tokens]


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
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # `ValueError` too: `_validate_browser_command` raises it for an empty
        # command, and `shlex.split` for an unbalanced quote. A config typo must
        # read as "this hook found nothing", like every other failure here —
        # but it must still *say so*. `download_pdf_with_browser` prints the
        # child's stderr on all six of its failure branches; this function read
        # `result.stderr` nowhere, so "install the playwright extra" reached the
        # user down one path and vanished down the other.
        print(f"browser hook could not run: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        child_stderr = getattr(result, "stderr", "")
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
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


#: Everything the child spends outside the challenge wait: browser launch, the
#: 60 s navigate, the candidate sweep afterwards, plus headroom for a cold
#: first-run profile copy.
_HOOK_OVERHEAD_SECONDS = 150

#: The parent's budget when the child is not waiting on a human.
_HOOK_DEFAULT_TIMEOUT_SECONDS = 180


def _hook_timeout_seconds(tokens: list[str]) -> int:
    """How long to let the browser hook run, given what it was asked to do.

    `pdf_planning` synthesizes `--headful --challenge-timeout 120` so a user can
    solve a CAPTCHA by hand. The child then spends up to 60 s navigating *plus*
    the full 120 s waiting, inside a fixed 180 s parent budget — so the wait was
    killed at the moment it became useful, and that is before counting browser
    startup or a first-run profile copy. The flow could not fit its own timeout.

    Read off the child's own arguments rather than hardcoded again here, so the
    two cannot drift apart.
    """
    for index, token in enumerate(tokens):
        if token == "--challenge-timeout" and index + 1 < len(tokens):
            try:
                challenge = int(tokens[index + 1])
            except ValueError:
                return _HOOK_DEFAULT_TIMEOUT_SECONDS
            if challenge > 0:
                return challenge + _HOOK_OVERHEAD_SECONDS
        elif token.startswith("--challenge-timeout="):
            try:
                challenge = int(token.split("=", 1)[1])
            except ValueError:
                return _HOOK_DEFAULT_TIMEOUT_SECONDS
            if challenge > 0:
                return challenge + _HOOK_OVERHEAD_SECONDS
    return _HOOK_DEFAULT_TIMEOUT_SECONDS


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
            timeout=_hook_timeout_seconds(tokens),
        )
    except subprocess.TimeoutExpired:
        print(
            "browser PDF hook timed out while trying to download PDF",
            file=sys.stderr,
        )
        return None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # `fetch_and_store_pdf_with_fallbacks` advances on a falsy return and has
        # no exception handling of its own, so every step in that chain must
        # report failure by returning None. Raising here — on a missing binary
        # (OSError), an unbalanced quote or empty command (ValueError) — aborted
        # the whole chain and skipped the FlareSolverr and desktop fallbacks,
        # which is worse than the misconfiguration itself. Note this is
        # reachable without any `browser_pdf_cmd` in config, because
        # `_auto_browser_pdf_cmd_for_url` synthesizes one for known hosts.
        print(f"browser PDF hook could not run: {exc}", file=sys.stderr)
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
