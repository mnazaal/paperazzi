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

    status = result.get("status", "ok")
    errors = list(result.get("errors", []) or [])
    if status == "error" and not errors:
        # `errors[]` is the documented failure channel, so a failure has to say
        # something in it. `fix merge` reported every one of its refusals as
        # `status: error` with a `message` and no errors at all, leaving a
        # consumer that branches on the channel looking at a failed command with
        # nothing wrong. Doing it here rather than per command means the next
        # service to forget is covered too.
        message = result.get("message")
        errors = [str(message)] if message else ["command failed"]

    envelope: dict[str, Any] = {
        "command": command,
        "status": status,
        "bib_name": result.get("bib_name"),
        "items": list(found_items) if found_items is not None else [],
        "errors": errors,
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


def merge_target_results(
    results: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    command: str,
) -> dict[str, Any]:
    """Combine per-library service results into one envelope-ready document.

    ``--target`` may be repeated, and a consumer should not have to branch on
    how many libraries were searched — but hand-building that document per
    command is what silently dropped keys the service reported: `search --json`
    lost the partial-parse `warnings` that text mode prints, and
    `update --promote --json` lost the `summary` carrying `provider_errors`.

    The rules are deliberately dull, so nothing needs to be remembered per
    command:

    - ``status`` is ``ok`` only when every target succeeded.
    - Items are concatenated, each stamped with the ``bib_name`` it came from.
    - Every *list*-valued key any result carried is concatenated under its own
      name, so nothing a service reported disappears.
    - Errors and warnings are prefixed with the target that produced them when
      more than one was addressed — "search failed" without saying *which*
      library failed is not actionable.
    - Any other key is taken from the first result that carried it.
    """
    ok = True
    names: list[str] = []
    items: list[Any] = []
    extras: dict[str, Any] = {}
    multiple = len(results) > 1

    for selector, result in results:
        if result.get("status") != "ok":
            ok = False
        bib_name = result.get("bib_name")
        label = bib_name if isinstance(bib_name, str) and bib_name else selector
        if isinstance(label, str) and label:
            names.append(label)

        consumed: set[str] = set()
        for key in _ITEM_KEYS:
            value = result.get(key)
            if isinstance(value, list):
                items.extend({**item, "bib_name": bib_name} if isinstance(item, dict) else item
                             for item in value)
                consumed.add(key)
                break

        for key, value in result.items():
            if key in consumed or key in {"status", "bib_name"}:
                continue
            if isinstance(value, list):
                prefixed = [
                    f"{label}: {entry}"
                    if multiple and key in {"errors", "warnings"} and isinstance(entry, str)
                    else entry
                    for entry in value
                ]
                extras.setdefault(key, []).extend(prefixed)
            else:
                extras.setdefault(key, value)

    return {
        "status": "ok" if ok else "error",
        "bib_name": ", ".join(names) if names else None,
        "items": items,
        "searched_bibs": names,
        **{k: v for k, v in extras.items() if k != "searched_bibs"},
        "command": command,
    }
