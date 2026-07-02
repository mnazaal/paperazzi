"""CLI output render helpers — pure formatters.

Each `_render_*` function takes a service result dict and returns one or
more lines of text.  No I/O, no side effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _error_lines(message: str, errors: Sequence[str]) -> list[str]:
    return [message, *(f"- {error}" for error in errors)]


def _render_add_success(result: Mapping[str, Any]) -> str:
    prefix = _dry_run_prefix(result)
    return f"{prefix}{result['action']} {result['citekey']} in {result['bib_name']}"


def _render_pdf_success(action: str, result: Mapping[str, Any]) -> str:
    return f"{action} PDF {result['citekey']} -> {result['local_pdf_path']}"


def _render_tag_mutation_success(result: Mapping[str, Any]) -> str:
    prefix = _dry_run_prefix(result)
    joined = ", ".join(result["tags"]) if result["tags"] else "(none)"
    return f"{prefix}{result['message']} for {result['citekey']}: {joined}"


def _render_search_matches(result: Mapping[str, Any]) -> list[str]:
    lines = []
    for match in result["matches"]:
        title = match["title"] or ""
        year = match["year"] if match["year"] is not None else ""
        fields = ",".join(match["matched_fields"])
        # "matched:" prefix disambiguates from `pzi entries`' 4th column, which
        # holds actual author names in the same tab-separated position — a bare
        # "[authors]" here would read as an author name, not a matched field.
        lines.append(f"{match['citekey']}\t{year}\t{title}\t[matched: {fields}]")
    return lines or ["no matches"]


_CHECK_SYMBOL = {"verified": "✓", "could_not_verify": "?", "problematic": "✗"}


def _render_check_items(result: Mapping[str, Any]) -> list[str]:
    # Problematic first, then could-not-verify, then verified — most actionable on top.
    order = {"problematic": 0, "could_not_verify": 1, "verified": 2}
    items = sorted(result["items"], key=lambda i: order.get(i["verdict"], 3))
    lines = []
    for item in items:
        symbol = _CHECK_SYMBOL.get(item["verdict"], "?")
        detail = ""
        if item["verdict"] != "verified":
            reason = item["mismatches"][0] if item["mismatches"] else item["verdict"]
            detail = f" — {reason}"
        lines.append(
            f"{symbol} {item['verdict']:<16} {item['citekey']} "
            f"({item['confidence_score']}/100){detail}"
        )
    counts = result["counts"]
    summary = (
        f"checked {result['total']}: {counts['verified']} verified, "
        f"{counts['could_not_verify']} could-not-verify, {counts['problematic']} problematic"
    )
    return [*(lines or ["no entries to check"]), summary]


def _render_bib_update_items(result: Mapping[str, Any]) -> list[str]:
    prefix = _dry_run_prefix(result)
    lines = []
    for item in result["items"]:
        changed = ", ".join(item["changed_fields"]) or "(no-op)"
        note = f" [{item['note']}]" if item["note"] else ""
        lines.append(f"{prefix}{item['citekey']}: {changed}{note}")
    return lines or [f"{prefix}no updates"]


def _render_bib_promote_items(result: Mapping[str, Any]) -> list[str]:
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
            + (f"; failed {summary['skipped_failed']}" if summary.get("skipped_failed") else "")
        )
        s2_warning = summary.get("s2_warning")
        if isinstance(s2_warning, str) and s2_warning:
            lines.append(f"{prefix}warning: {s2_warning}")
    return lines


def _dry_run_prefix(result: Mapping[str, Any]) -> str:
    return "DRY RUN: " if result["dry_run"] else ""


def _render_bib_stats(result: Mapping[str, Any]) -> list[str]:
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


def _render_clean_result(result: Mapping[str, Any], dry_run: bool) -> list[str]:
    """Render clean/validate result as human-readable lines."""
    prefix = "DRY RUN: " if dry_run else ""
    lines = [
        f"path: {result['bib_path']}",
        f"entries: {result['total_entries']}",
    ]
    if result.get("duplicate_citekeys"):
        lines.append(f"duplicate citekeys: {', '.join(result['duplicate_citekeys'])}")
    if result.get("missing_pdfs"):
        lines.append(f"missing PDFs: {len(result['missing_pdfs'])}")
    if result.get("orphan_pdfs"):
        lines.append(f"orphan PDFs: {len(result['orphan_pdfs'])}")

    if result.get("issues"):
        lines.append(f"issues ({len(result['issues'])}):")
        for issue in result["issues"]:
            sev = issue["severity"].upper()
            lines.append(f"  [{sev}] {issue['message']}")
    else:
        lines.append("no issues found")

    if result.get("actions"):
        lines.append(f"{prefix}actions ({len(result['actions'])}):")
        for action in result["actions"]:
            typ = action["type"]
            done = "done" if action.get("done") else "would do"
            lines.append(f"  {prefix}{done}: {typ}")

    return lines


def _render_dedupe_result(result: Mapping[str, Any]) -> list[str]:
    """Render dedupe result as human-readable lines."""
    lines = [
        f"path: {result['bib_path']}",
        f"entries: {result['total_entries']}",
        f"exact duplicate clusters: {result['total_clusters']}",
    ]
    for cluster in result.get("exact_duplicates", []):
        lines.append(f"  {', '.join(cluster['citekeys'])}")
    fuzzy = result.get("fuzzy_candidates", [])
    if fuzzy:
        lines.append(f"fuzzy near-duplicates: {len(fuzzy)}")
        for cand in fuzzy:
            lines.append(f"  {cand['citekey']} → similar to {cand['hint']}")
    return lines


def _render_reindex_result(result: Mapping[str, Any], dry_run: bool) -> list[str]:
    """Render reindex result as human-readable lines."""
    prefix = "DRY RUN: " if dry_run else ""
    lines = [f"bib: {result['bib_path']}", f"entries: {result['total_entries']}"]
    changed = result.get("changed", [])
    if changed:
        lines.append(f"{prefix}changed citekeys ({len(changed)}):")
        for ch in changed:
            pdf_note = " [PDF renamed]" if ch.get("renamed_pdf") else ""
            lines.append(f"  {ch['old_citekey']} → {ch['new_citekey']}{pdf_note}")
    else:
        lines.append("no citekey changes needed")
    for err in result.get("errors", []):
        lines.append(f"error: {err}")
    return lines


def _render_delete_success(result: Mapping[str, Any]) -> str:
    """Render delete result as a single status line."""
    prefix = "DRY RUN: " if result["dry_run"] else ""
    msg = result["message"]
    pdf = f" (PDF at {result['pdf_path']})" if result.get("pdf_path") else ""
    return f"{prefix}{msg}{pdf}"
