"""External page metadata processor support.

Contract, shared with the two sibling `*_cmd` launchers
(``pzi.capture_context.run_shell_command`` for the four secret-resolving
config keys, and ``pzi.browser_pdf`` for ``browser_pdf_cmd``, another lane's
file): a command that **cannot be run at all** — unparseable, empty, a
missing or non-executable binary — is a configuration mistake and raises
:class:`~pzi.errors.PziError` with ``reason=REASON_CONFIG``, naming the config
key, the offending token, and what to do.

``browser_pdf_cmd`` is the one deliberate exception to "raise, don't return a
sentinel": it returns ``None`` on *every* launch failure, including the ones
classified as ``PziError`` everywhere else, because raising there would abort
the FlareSolverr and desktop fallbacks `pdf.py` runs after it. That is a
considered choice recorded where the divergence would otherwise be
rediscovered as a bug, not an oversight — see `browser_pdf.py`'s
`discover_pdf_url_with_browser` and `download_pdf_with_browser`.

Unlike the four secret commands, this hook's argument text is not screened
for shell metacharacters. `capture_context.run_shell_command` screens because
a `*_cmd` there fetches a secret the user often pastes from a password-manager
doc that assumes real shell syntax (`&&`, backticks); failing that loudly is
worth it. A page-metadata hook is a fixed script the user wrote for this
purpose, not a copy-pasted one-liner, and every mode here already runs
`shell=False`, so the character itself was never going to be interpreted —
screening it would only reject a legitimate argument. Still a considered
choice, not an inconsistency: browser_pdf_cmd screens neither, so absent
screening is the majority position of the three.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Any

from pzi.errors import REASON_CONFIG, PziError

# Control characters (U+0000-U+001F, excluding tab/CR/LF) stripped from
# forwarded child stderr before it reaches a terminal. Mirrors
# `browser_pdf._safe_stderr` (`browser_pdf.py`), duplicated rather than
# imported: that module belongs to another lane, and two lines are cheaper
# than a cross-lane import for this phase.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _safe_stderr(text: str) -> str:
    return _CONTROL_RE.sub("", text)


def run_page_metadata_cmd(
    command: str,
    *,
    url: str,
    html: str,
    current_metadata: Mapping[str, object],
    timeout_seconds: int = 5,
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, object]:
    """Run external metadata command and parse object JSON from stdout.

    Command receives JSON on stdin:
    {"url": ..., "html": ..., "metadata": {...}}

    A command that *ran and failed* — a non-zero exit, a timeout, or stdout
    that is not a JSON object — is tolerated: it returns ``{}`` and the
    capture proceeds without the hook, forwarding the child's stderr
    (control-stripped) so the user learns why, the way `browser_pdf.py`
    forwards its hook's stderr. A command that cannot be run at all is a
    configuration mistake; see the module docstring for the shared contract.
    """
    payload = json.dumps(
        {"url": url, "html": html, "metadata": dict(current_metadata)},
        sort_keys=True,
    )
    try:
        # `shell=False` means the shell never expands `~` — expand it here, so
        # `page_metadata_cmd = "~/bin/hook"` works the same as it already does
        # for `browser_pdf_cmd` (`browser_pdf.resolve_browser_command`).
        argv = [os.path.expanduser(token) for token in shlex.split(command)]
    except ValueError as exc:
        # An unclosed quote in the configured command. `shlex.split` raises, and
        # nothing caught it: a raw traceback on the CLI and a 500 on the HTTP
        # API, for a typo in config.toml.
        raise PziError(
            f"page_metadata_cmd could not be parsed: {exc}", reason=REASON_CONFIG
        ) from exc
    if not argv:
        # A whitespace-only config value. `shlex.split(" ") == []`, and
        # `subprocess.run([])` raises IndexError — the one failure mode that
        # does not even name the command it came from.
        raise PziError(
            "page_metadata_cmd is empty; remove it or give it a command",
            reason=REASON_CONFIG,
        )
    try:
        result = run(
            argv,
            input=payload,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {}
    except OSError as exc:
        # A missing binary, or one that is not executable. Only
        # `TimeoutExpired` was caught, so this was a traceback rather than the
        # "your hook is misconfigured" message it is. Phrased "could not run",
        # matching `browser_pdf.py` and `capture_context.run_shell_command` —
        # it said "could not *be* run" here, one word off for the same fault.
        raise PziError(
            f"page_metadata_cmd could not run: {exc}", reason=REASON_CONFIG
        ) from exc
    if getattr(result, "returncode", 1) != 0:
        # Ran and failed. Forward the child's stderr (control-stripped) so the
        # user learns why — this used to discard it and return `{}` silently,
        # while `browser_pdf.py`'s identical hook contract already forwards it.
        child_stderr = getattr(result, "stderr", "") or ""
        if child_stderr:
            print(_safe_stderr(child_stderr), end="", file=sys.stderr)
        return {}
    try:
        parsed = json.loads(getattr(result, "stdout", "") or "")
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
