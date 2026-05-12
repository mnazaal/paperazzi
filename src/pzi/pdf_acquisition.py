"""Helpers for planning PDF discovery candidates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias

PdfCandidate: TypeAlias = dict[str, Any]



def landing_page_urls(
    *, base_record: Mapping[str, object], raw_value: str
) -> list[str]:
    candidates: list[str] = []
    for value in [
        base_record.get("canonical_url"),
        base_record.get("source_url"),
        base_record.get("abstract_url"),
        raw_value,
    ]:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            continue
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def pdf_candidates_from_record(
    *, base_record: Mapping[str, object], raw_value: str
) -> list[PdfCandidate]:
    pdf_url = base_record.get("pdf_url")
    if isinstance(pdf_url, str) and pdf_url.strip():
        return [{"source": "record", "url": pdf_url.strip()}]

    return [
        {"source": "landing_page", "url": url}
        for url in landing_page_urls(base_record=base_record, raw_value=raw_value)
    ]
