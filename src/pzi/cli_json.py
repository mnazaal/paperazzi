"""JSON output helpers for the CLI.

Under ``--json`` a command emits exactly one JSON document on stdout, whether it
succeeded or failed, so a consumer never has to fall back to scraping stderr for
the cases it most needs to classify (rate-limited, bad DOI, locked bib).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TextIO


def emit(payload: Mapping[str, Any] | Sequence[Any], stdout: TextIO) -> None:
    """Write one JSON document to *stdout*."""
    print(json.dumps(payload, indent=2, default=str), file=stdout)


def emit_error(message: str, errors: Sequence[str], stdout: TextIO) -> None:
    """Write a failure as JSON, in the same shape services already return."""
    emit({"status": "error", "message": message, "errors": list(errors)}, stdout)
