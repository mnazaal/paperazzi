"""Shared capture core adapter.

This module maps pure capture request models to existing add-service calls.
It deliberately has no CLI, HTTP, or browser-extension imports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pzi.add_service import add_input_to_bib
from pzi.capture_models import (
    CaptureInput,
    CaptureOptions,
    normalize_pdf_candidates,
    rank_pdf_candidates,
)
from pzi.html_metadata import extract_metadata_from_html
from pzi.page_metadata_cmd import run_page_metadata_cmd
from pzi.url_safety import safe_public_http_url

CaptureResult = Mapping[str, Any]


def capture_to_bib(
    capture: CaptureInput,
    options: CaptureOptions,
    *,
    config_path: str,
    home_dir: str,
    add_fn: Callable[..., CaptureResult] = add_input_to_bib,
    page_metadata_cmd_fn: Callable[..., dict[str, object]] = run_page_metadata_cmd,
    service_kwargs: Mapping[str, Any] | None = None,
) -> CaptureResult:
    """Capture one item into a BibTeX library through existing add service."""
    record_overrides = _record_overrides_with_page_fallbacks(
        capture,
        options,
        page_metadata_cmd_fn=page_metadata_cmd_fn,
    )
    pdf_candidates = [
        candidate.value
        for candidate in rank_pdf_candidates(
            normalize_pdf_candidates(capture.pdf_candidates)
        )
    ]
    page_pdf_url = record_overrides.get("fallback_pdf_url")
    if (
        isinstance(page_pdf_url, str)
        and page_pdf_url not in pdf_candidates
        and safe_public_http_url(page_pdf_url)
    ):
        pdf_candidates.append(page_pdf_url)

    kwargs: dict[str, Any] = {
        "config_path": config_path,
        "home_dir": home_dir,
        "value": capture.value,
        "record_overrides": record_overrides,
        "bib_selector": capture.bib_selector,
        "dry_run": options.dry_run,
        "force_new": options.force_new,
        "metadata_strict": options.metadata_strict,
    }
    if pdf_candidates:
        kwargs["pdf_url_candidates"] = pdf_candidates
    if capture.auth_hints.cookies:
        kwargs["cookies"] = capture.auth_hints.cookies
    if service_kwargs:
        kwargs.update(dict(service_kwargs))
    return add_fn(**kwargs)


def _record_overrides_with_page_fallbacks(
    capture: CaptureInput,
    options: CaptureOptions,
    *,
    page_metadata_cmd_fn: Callable[..., dict[str, object]] = run_page_metadata_cmd,
) -> dict[str, object]:
    """Merge page HTML metadata as fallback_* overrides."""
    merged = dict(capture.record_overrides)
    if capture.page_artifact is None:
        return merged
    page_record = extract_metadata_from_html(capture.page_artifact.html)
    if page_record:
        _merge_fallback_metadata(merged, page_record)
    if options.page_metadata_cmd:
        external_record = page_metadata_cmd_fn(
            options.page_metadata_cmd,
            url=capture.value,
            html=capture.page_artifact.html,
            current_metadata=page_record or {},
            timeout_seconds=options.page_metadata_timeout_seconds,
        )
        _merge_fallback_metadata(merged, external_record)
    return merged


def _merge_fallback_metadata(
    merged: dict[str, object],
    metadata: Mapping[str, object],
) -> None:
    for key in ("title", "authors", "year", "venue", "doi", "pdf_url"):
        value = metadata.get(key)
        if value:
            target = "fallback_pdf_url" if key == "pdf_url" else f"fallback_{key}"
            merged.setdefault(target, value)
