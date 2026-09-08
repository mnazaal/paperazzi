"""Optional external headless-browser PDF discovery and download hook."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys

#: The module a pzi-owned hook command runs. The interpreter in front of it is
#: interchangeable *because* the code behind it is ours; see
#: `resolve_browser_command`.
_OWN_HOOK_MODULE = "pzi.browser_pdf_hook"

#: Stale interpreters already announced, so a `--failed-only` sweep across 200
#: entries says it once rather than once per entry.
_notified: set[str] = set()


def resolve_browser_command(command: str) -> list[str]:
    """Split a browser PDF hook command into argv, raising on unusable input.

    ``shell=False`` execution means the shell never expands ``~``, so do it
    here: this lets ``config.toml`` carry ``~/...`` paths (e.g. the interpreter
    or a browser ``--profile``) instead of absolute home paths. Tokens without a
    leading ``~`` are unchanged.
    """
    tokens = shlex.split(command)
    if not tokens:
        raise ValueError("empty browser command in config")
    return [os.path.expanduser(token) for token in tokens]


def healed_tokens(tokens: list[str]) -> list[str] | None:
    """*tokens* with a stale interpreter replaced, or None if that is not safe.

    ``pzi init --setup`` used to bake the *then-current* ``sys.executable`` into
    ``browser_pdf_cmd``. That path is an install-time snapshot: renaming the
    distribution, a reinstall under a different tool directory, a Python upgrade,
    or another machine sharing the same dotfiles each leave it naming an
    interpreter that no longer exists. Setup no longer writes it, but configs in
    the field still carry it — this is what makes those keep working.

    Substituting is safe **only because the module being run is pzi's own**: the
    interpreter that can import `pzi.browser_pdf_hook` is by definition the one
    running this code. The reasoning does not extend one step further, so a
    third-party hook that has gone missing returns None and is reported as the
    configuration fault it is.
    """
    if tokens[1:3] != ["-m", _OWN_HOOK_MODULE]:
        return None
    if tokens[0] == sys.executable:
        return None  # already us; the failure was something else
    return [sys.executable, *tokens[1:]]


def _announce_substitution(stale: str) -> None:
    """Say once per process that a stale interpreter was replaced.

    Once, not once per entry: `pdf retry --failed-only` runs this hook for every
    PDF-less entry in the library, and a per-entry notice buries the summary it
    is trying to explain.
    """
    if stale in _notified:
        return
    _notified.add(stale)
    print(
        f"browser_pdf_cmd names an interpreter that no longer exists ({stale}); "
        f"using {sys.executable} instead. Remove the browser_pdf_cmd line from "
        "your config to stop pinning it — pzi builds the command at run time.",
        file=sys.stderr,
    )


def _run_browser_hook(command: str, payload: str) -> subprocess.CompletedProcess[str]:
    """Run the hook, retrying once with the running interpreter if argv[0] is gone.

    One body for both hook directions. They had two copies of the split, the run
    and the timeout, and the copies had already drifted — only one of them read
    the child's stderr. Healing added a third thing to keep in step, so the
    copies were merged instead.

    The staleness check is the failure itself, not a `which` probe beforehand:
    `subprocess.run` already answers "can this be executed", and asking twice is
    how the two answers get to disagree.
    """
    tokens = resolve_browser_command(command)
    try:
        return subprocess.run(
            tokens,
            input=payload,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_hook_timeout_seconds(tokens),
        )
    except FileNotFoundError:
        healed = healed_tokens(tokens)
        if healed is None:
            raise
        _announce_substitution(tokens[0])
        return subprocess.run(
            healed,
            input=payload,
            shell=False,
            capture_output=True,
            text=True,
            timeout=_hook_timeout_seconds(healed),
        )


# Control characters (U+0000-U+001F) — stripped from subprocess stderr
# before printing to prevent terminal escape injection.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _safe_stderr(text: str) -> str:
    """Strip terminal control characters from subprocess stderr output."""
    return _CONTROL_RE.sub("", text)


def discover_pdf_url_with_browser(
    *,
    command: str,
    page_url: str,
    doi: str | None = None,
    errors: list[str] | None = None,
) -> str | None:
    """Discover PDF URL from a page using external browser hook.

    *errors* collects the reason this stage produced nothing, the way
    `download_via_server_api` already does for the server-browser rung. A hook
    that could not *start* and a hook that ran and found no PDF are different
    facts, and the caller reported both as "no PDF returned".
    """
    payload = json.dumps({"page_url": page_url, "doi": doi})
    try:
        result = _run_browser_hook(command, payload)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # `ValueError` too: `resolve_browser_command` raises it for an empty
        # command, and `shlex.split` for an unbalanced quote. A config typo must
        # read as "this hook found nothing", like every other failure here —
        # but it must still *say so*. `download_pdf_with_browser` forwards the
        # child's stderr on every failure; this function read `result.stderr`
        # nowhere, so "install the playwright extra" reached the user down one
        # path and vanished down the other.
        message = f"browser hook could not run: {exc}"
        print(message, file=sys.stderr)
        if errors is not None:
            errors.append(message)
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
    *, command: str, pdf_url: str, errors: list[str] | None = None
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
        result = _run_browser_hook(command, payload)
    except subprocess.TimeoutExpired:
        message = "browser PDF hook timed out while trying to download PDF"
        print(message, file=sys.stderr)
        if errors is not None:
            errors.append(message)
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
        message = f"browser PDF hook could not run: {exc}"
        print(message, file=sys.stderr)
        if errors is not None:
            errors.append(message)
        return None
    pdf_bytes = _decode_hook_pdf(result)
    if pdf_bytes is not None:
        return pdf_bytes
    # One forwarding site, not one per failure branch: every way this can fail
    # -- non-zero exit, empty stdout, unparseable JSON, no `pdf_base64`, bad
    # base64, bytes that are not a PDF -- wants the child's own diagnostics on
    # stderr, and the six copies were how one of them lost it.
    child_stderr = getattr(result, "stderr", "")
    if child_stderr:
        print(_safe_stderr(child_stderr), end="", file=sys.stderr)
    return None


def _decode_hook_pdf(result: subprocess.CompletedProcess[str]) -> bytes | None:
    """PDF bytes from a completed hook run, or None if it did not produce any."""
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
    try:
        pdf_bytes = base64.b64decode(pdf_base64, validate=True)
    except (ValueError, TypeError):
        return None
    return pdf_bytes if pdf_bytes.startswith(b"%PDF-") else None
