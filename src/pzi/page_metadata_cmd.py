"""External page metadata processor support."""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable, Mapping
from typing import Any


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
    """
    payload = json.dumps(
        {"url": url, "html": html, "metadata": dict(current_metadata)},
        sort_keys=True,
    )
    try:
        result = run(
            shlex.split(command),
            input=payload,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {}
    if getattr(result, "returncode", 1) != 0:
        return {}
    try:
        parsed = json.loads(getattr(result, "stdout", "") or "")
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
