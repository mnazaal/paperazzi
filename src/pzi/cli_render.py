"""Pure CLI output rendering helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def error_lines(message: str, errors: Sequence[str]) -> list[str]:
    return [message, *(f"- {error}" for error in errors)]


def render_add_success(result: Mapping[str, Any]) -> str:
    prefix = _dry_run_prefix(result)
    return f"{prefix}{result['action']} {result['citekey']} in {result['bib_name']}"


def render_pdf_success(action: str, result: Mapping[str, Any]) -> str:
    return f"{action} PDF {result['citekey']} -> {result['local_pdf_path']}"


def render_tag_mutation_success(result: Mapping[str, Any]) -> str:
    prefix = _dry_run_prefix(result)
    joined = ", ".join(result["tags"]) if result["tags"] else "(none)"
    return f"{prefix}{result['message']} for {result['citekey']}: {joined}"


def render_search_matches(result: Mapping[str, Any]) -> list[str]:
    lines = []
    for match in result["matches"]:
        title = match["title"] or ""
        year = match["year"] if match["year"] is not None else ""
        fields = ",".join(match["matched_fields"])
        lines.append(f"{match['citekey']}\t{year}\t{title}\t[{fields}]")
    return lines or ["no matches"]


def render_bib_list(result: Mapping[str, Any]) -> list[str]:
    lines = []
    for bib in result["bibs"]:
        marker = " (default)" if bib["default"] else ""
        lines.append(f"{bib['name']}\t{bib['path']}{marker}")
    return lines


def render_bib_update_items(result: Mapping[str, Any]) -> list[str]:
    prefix = _dry_run_prefix(result)
    lines = []
    for item in result["items"]:
        changed = ", ".join(item["changed_fields"]) or "(no-op)"
        note = f" [{item['note']}]" if item["note"] else ""
        lines.append(f"{prefix}{item['citekey']}: {changed}{note}")
    return lines or [f"{prefix}no updates"]


def render_bib_promote_items(result: Mapping[str, Any]) -> list[str]:
    prefix = _dry_run_prefix(result)
    lines = []
    for item in result["items"]:
        changed = ", ".join(item["changed_fields"]) or "(no-op)"
        note = f" [{item['note']}]" if item["note"] else ""
        pdf = " [PDF]" if item["pdf_attached"] else ""
        preprint = item["preprint_citekey"]
        pub = item["published_citekey"] or preprint
        action = item.get("action")
        if action == "create":
            lines.append(
                f"{prefix}{preprint}: kept preprint, created {pub}: {changed}{pdf}{note}"
            )
        elif action == "update":
            lines.append(
                f"{prefix}{preprint}: replaced preprint metadata in-place: "
                f"{changed}{pdf}{note}"
            )
        else:
            lines.append(f"{prefix}{preprint} -> {pub}: {changed}{pdf}{note}")
    if not lines:
        lines = [f"{prefix}no preprints to promote"]
    summary = result.get("summary")
    if isinstance(summary, Mapping):
        lines.append(
            f"{prefix}summary: checked {summary['checked']}; "
            f"created {summary['created']}; updated {summary['updated']}; "
            f"no candidate {summary['skipped_no_candidate']}; "
            f"low confidence {summary['skipped_low_confidence']}; "
            f"existing {summary['skipped_existing']}; "
            f"provider errors {summary['provider_errors']}"
        )
    return lines


def _dry_run_prefix(result: Mapping[str, Any]) -> str:
    return "DRY RUN: " if result["dry_run"] else ""


def render_bib_stats(result: Mapping[str, Any]) -> list[str]:
    """Render bib-stats result as human-readable lines."""
    lines = [
        f"path: {result['bib_path']}",
        f"entries: {result['total_entries']}",
        f"with PDF: {result['with_pdf']}",
        f"with DOI: {result['with_doi']}",
        f"with arXiv ID: {result['with_arxiv_id']}",
        f"preprints: {result['preprints']}",
    ]
    entry_types = result.get("entry_types", {})
    if entry_types:
        type_line = "entry types: " + ", ".join(
            f"{k}: {v}" for k, v in sorted(entry_types.items())
        )
        lines.append(type_line)
    return lines


def render_delete_success(result: Mapping[str, Any]) -> str:
    """Render delete result as a single status line."""
    prefix = "DRY RUN: " if result["dry_run"] else ""
    msg = result["message"]
    pdf = f" (PDF at {result['pdf_path']})" if result.get("pdf_path") else ""
    return f"{prefix}{msg}{pdf}"
