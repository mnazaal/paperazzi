"""External page metadata processor support."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from pzi.errors import REASON_CONFIG, PziError


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

    A command that *fails* is tolerated — a non-zero exit, a timeout, or stdout
    that is not a JSON object returns ``{}`` and the capture proceeds without
    the hook. A command that cannot be run at all is a configuration mistake and
    raises :class:`~pzi.errors.PziError`: only ``TimeoutExpired`` was caught, so
    a missing binary (``FileNotFoundError``), a whitespace-only config value
    (``shlex.split(" ") == []`` → ``subprocess.run([])`` → ``IndexError``) and
    an unclosed quote (``ValueError``) were each a raw traceback on the CLI and
    a 500 on the HTTP API.
    """
    payload = json.dumps(
        {"url": url, "html": html, "metadata": dict(current_metadata)},
        sort_keys=True,
    )
    try:
        argv = shlex.split(command)
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
        # "your hook is misconfigured" message it is.
        raise PziError(
            f"page_metadata_cmd could not be run: {exc}", reason=REASON_CONFIG
        ) from exc
    if getattr(result, "returncode", 1) != 0:
        return {}
    try:
        parsed = json.loads(getattr(result, "stdout", "") or "")
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
