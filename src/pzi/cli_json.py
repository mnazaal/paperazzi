"""JSON output helpers for the CLI.

Under ``--json`` a command emits exactly one JSON document on stdout, whether it
succeeded or failed, so a consumer never has to fall back to scraping stderr for
the cases it most needs to classify (missing citekey, unresolvable target,
locked bib).

Every document is the same envelope::

    {"command": "search", "status": "ok", "bib_name": "ml",
     "items": [...], "errors": []}

so ``jq '.items[]'`` works against any command.  Whatever else the service
reported (counts like ``imported``, flags like ``dry_run``) rides along beside
those five keys rather than being dropped.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

# Service result keys that hold "the list of things this command produced".
# They are normalized to `items` so consumers do not need a per-command jq path.
_ITEM_KEYS: tuple[str, ...] = ("items", "matches", "results", "bibs")

_ENVELOPE_KEYS = frozenset({"command", "status", "bib_name", "items", "errors"})


def build_envelope(
    result: Mapping[str, Any],
    *,
    command: str,
    items: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Normalize a service result into the standard envelope.

    Pure.  Pass *items* when the command's list does not live under one of the
    usual keys (e.g. a single record rendered as a one-item list).
    """
    found_items: Sequence[Any] | None = items
    consumed: set[str] = set()
    if found_items is None:
        for key in _ITEM_KEYS:
            value = result.get(key)
            if isinstance(value, list):
                found_items = value
                consumed.add(key)
                break

    envelope: dict[str, Any] = {
        "command": command,
        "status": result.get("status", "ok"),
        "bib_name": result.get("bib_name"),
        "items": list(found_items) if found_items is not None else [],
        "errors": list(result.get("errors", []) or []),
    }
    # Everything the service reported that the envelope does not already carry.
    for key, value in result.items():
        if key in _ENVELOPE_KEYS or key in consumed or key == "errors":
            continue
        envelope[key] = value
    return envelope


def _emit(payload: Mapping[str, Any] | Sequence[Any], stdout: TextIO) -> None:
    """Write one JSON document to *stdout*.

    Private: it guarantees only "valid JSON", not the envelope, so every one of
    its former direct callers was a contract violation. Reach it through
    :func:`emit_result` or :func:`emit_error`, which stamp the five keys.
    """
    print(json.dumps(payload, indent=2, default=str), file=stdout)


def emit_result(
    result: Mapping[str, Any],
    stdout: TextIO,
    *,
    command: str,
    items: Sequence[Any] | None = None,
) -> None:
    """Write a service result to *stdout* as the standard envelope."""
    _emit(build_envelope(result, command=command, items=items), stdout)


def emit_error(message: str, errors: Sequence[str], stdout: TextIO, *, command: str) -> None:
    """Write a failure as the standard envelope."""
    emit_result(
        {"status": "error", "message": message, "errors": list(errors)},
        stdout,
        command=command,
    )
