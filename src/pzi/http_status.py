"""HTTP status mapping for service result dictionaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def status_for_service_result(
    result: Mapping[str, Any],
    *,
    default_error_status: int = 400,
) -> int:
    """Map common service result shapes to HTTP status codes.

    Services return small dicts with ``status``, ``message``, and/or ``errors``.
    Keep mapping here so route modules do not each invent their own policy.
    """
    if result.get("status") == "ok":
        return 200

    text = _result_text(result).lower()
    if "config" in text or "bib not found" in text or "library" in text:
        return 400
    if "not found" in text or "no such" in text:
        return 404
    if "not available" in text or "unavailable" in text:
        return 503
    return default_error_status


def _result_text(result: Mapping[str, Any]) -> str:
    parts: list[str] = []
    message = result.get("message")
    if isinstance(message, str):
        parts.append(message)
    error = result.get("error")
    if isinstance(error, str):
        parts.append(error)
    errors = result.get("errors")
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
        parts.extend(str(item) for item in errors)
    return "\n".join(parts)
