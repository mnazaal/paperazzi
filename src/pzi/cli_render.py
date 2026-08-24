"""CLI output render helpers — pure formatters.

Each `_render_*` function takes a service result dict and returns one or
more lines of text.  No I/O, no side effects.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


def distinct_details(message: str, details: Sequence[str]) -> list[str]:
    """*details* with anything that merely repeats *message* removed.

    Plenty of services set ``message`` and ``errors`` to the same string — there
    is only one thing to say — and a renderer that prints the headline and then
    bullets every error shows that sentence twice, which reads as two separate
    failures. `pzi export` against a missing `--target` did exactly that.

    One function because there are two such renderers: `error_lines` here and
    the `--json`-aware `_fail` in `pzi.cli`, which print different shapes but
    need the same rule.
    """
    return [detail for detail in details if detail.strip() != message.strip()]


def error_lines(message: str, errors: Sequence[str]) -> list[str]:
    """A headline followed by one bullet per error that adds something."""
    return [message, *(f"- {error}" for error in distinct_details(message, errors))]


def render_add_success(result: Mapping[str, Any]) -> str:
    prefix = _dry_run_prefix(result)
    return f"{prefix}{result['action']} {result['citekey']} in {result['bib_name']}"


def render_pdf_success(action: str, result: Mapping[str, Any]) -> str:
    return f"{action} PDF {result['citekey']} -> {result['local_pdf_path']}"


def render_tag_mutation_success(result: Mapping[str, Any]) -> str:
    prefix = _dry_run_prefix(result)
    joined = ", ".join(result["tags"]) if result["tags"] else "(none)"
    return f"{prefix}{result['message']} for {result['citekey']}: {joined}"


#: Everything a terminal or a `cut -f` reader treats as structure: the column
#: separator, the row separator, and the control characters an ANSI escape is
#: built from. All three are values a *captured page* can put in a title, so the
#: rendering layer neutralizes them — the stored entry keeps them verbatim.
_UNSAFE_IN_ROW = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def render_cell(value: object) -> str:
    """One field of a tab-separated row, with its structure characters removed.

    A title carrying a tab shifted every later column by one; one carrying a
    newline invented an entire extra row, which a script reading `pzi search`
    would parse as another entry. Replaced with spaces rather than dropped, so
    the text stays readable.
    """
    text = "" if value is None else str(value)
    return _UNSAFE_IN_ROW.sub("", text.replace("\t", " ").replace("\n", " ").replace("\r", " "))


def render_search_matches(result: Mapping[str, Any]) -> list[str]:
    lines = []
    for match in result["matches"]:
        title = match["title"] or ""
        year = match["year"] if match["year"] is not None else ""
        fields = ",".join(match["matched_fields"])
        # "matched:" prefix disambiguates from `pzi entries`' 4th column, which
        # holds actual author names in the same tab-separated position — a bare
        # "[authors]" here would read as an author name, not a matched field.
        lines.append(
            f"{render_cell(match['citekey'])}\t{render_cell(year)}\t"
            f"{render_cell(title)}\t[matched: {render_cell(fields)}]"
        )
    # No placeholder line: an empty result means empty stdout, so the output
    # pipes cleanly into xargs/awk. The runner reports "no matches" on stderr.
    return lines


_CHECK_SYMBOL = {"verified": "✓", "could_not_verify": "?", "problematic": "✗"}


def render_check_items(result: Mapping[str, Any]) -> list[str]:
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
        backup = item.get("backup_path")
        if isinstance(backup, str):
            # An undo the user cannot find is not an undo — `delete` says the
            # same thing for the same reason.
            lines.append(f"{prefix}  backup saved to {backup}")
    summary = result.get("summary")
    if not lines:
        # "no preprints to promote" is false when there were preprints and the
        # run deliberately passed over them. Saying which is the difference
        # between "nothing to do" and "nothing done yet".
        skipped = 0
        if isinstance(summary, Mapping):
            skipped = (summary.get("skipped_recently_checked") or 0) + (
                summary.get("skipped_already_resolved") or 0
            )
        lines = [
            f"{prefix}no preprints to promote"
            if not skipped
            else f"{prefix}no preprints left to promote ({skipped} skipped)"
        ]
    if isinstance(summary, Mapping):
        lines.append(
            f"{prefix}summary: checked {summary['checked']}; "
            f"created {summary['created']}; updated {summary['updated']}; "
            f"no candidate {summary['skipped_no_candidate']}; "
            f"low confidence {summary['skipped_low_confidence']}; "
            f"existing {summary['skipped_existing']}; "
            f"provider errors {summary['provider_errors']}"
            + (f"; failed {summary['skipped_failed']}" if summary.get("skipped_failed") else "")
            # Shown only when non-zero, like `failed`: a skip the user did not
            # ask for needs explaining, and one that never happened is noise.
            + (
                f"; recently checked {summary['skipped_recently_checked']}"
                if summary.get("skipped_recently_checked")
                else ""
            )
            + (
                f"; already resolved {summary['skipped_already_resolved']}"
                if summary.get("skipped_already_resolved")
                else ""
            )
        )
        s2_warning = summary.get("s2_warning")
        if isinstance(s2_warning, str) and s2_warning:
            lines.append(f"{prefix}warning: {s2_warning}")
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


def render_clean_result(result: Mapping[str, Any], dry_run: bool) -> list[str]:
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
            if action.get("error"):
                # A *failed* move rendered as "would do" — the dry-run wording —
                # so a real run that could not move anything read exactly like a
                # preview of one that would.
                lines.append(f"  failed: {typ}: {action['error']}")
                continue
            done = "done" if action.get("done") else "would do"
            # Naming the file is the whole point of the preview: "would do:
            # move_orphan" describes a command that moves files without saying
            # which one, while `--json` carried `source` and `destination` all
            # along.
            source = action.get("source")
            destination = action.get("destination")
            detail = f": {source}" if source else ""
            if source and destination:
                detail = f": {source} -> {destination}"
            lines.append(f"  {prefix}{done}: {typ}{detail}")

    return lines


def render_dedupe_result(result: Mapping[str, Any]) -> list[str]:
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


def render_reindex_result(result: Mapping[str, Any], dry_run: bool) -> list[str]:
    """Render reindex result as human-readable lines."""
    prefix = "DRY RUN: " if dry_run else ""
    lines = [f"bib: {result['bib_path']}", f"entries: {result['total_entries']}"]
    changed = result.get("changed", [])
    if changed:
        lines.append(f"{prefix}changed citekeys ({len(changed)}):")
        for ch in changed:
            lines.append(f"  {ch['old_citekey']} → {ch['new_citekey']}")
            # Name the PDF being moved: a rename that picks up the wrong file is
            # only visible in a dry run if the paths are shown.
            if ch.get("renamed_pdf") and ch.get("old_pdf"):
                lines.append(f"    PDF: {ch['old_pdf']} → {ch['new_pdf']}")
    else:
        lines.append("no citekey changes needed")
    return lines


def reindex_error_lines(result: Mapping[str, Any]) -> list[str]:
    """`library reindex`'s per-PDF rename errors, for stderr.

    They used to be appended to the rendered table and printed to stdout, alone
    among the runners — so `pzi library reindex > report.txt` put the errors in the
    report and left the terminal clean, the opposite of every other command.
    """
    return [f"error: {err}" for err in result.get("errors", [])]


def render_delete_success(result: Mapping[str, Any]) -> str:
    """Render delete result as a single status line."""
    prefix = "DRY RUN: " if result["dry_run"] else ""
    msg = result["message"]
    pdf = f" (PDF at {result['pdf_path']})" if result.get("pdf_path") else ""
    return f"{prefix}{msg}{pdf}"


def render_doctor_result(result: Mapping[str, Any]) -> list[str]:
    """Render `pzi doctor` as status lines for a human."""
    ok = "ok"
    bad = "FAIL"
    lines = [f"config: {ok if result.get('config_ok') else bad} ({result.get('config_path')})"]
    for err in result.get("config_errors", []):
        lines.append(f"  - {err}")
    # A key pzi does not know is not fatal — a config written for a newer pzi
    # still loads — but a *typo'd* one silently does nothing, which is the exact
    # failure this command exists to find. `doctor --config-only` reported these
    # all along; the plain run computed them and dropped them.
    for warning in result.get("config_warnings", []):
        lines.append(f"  - warning: {warning}")

    for bib in result.get("bibs", []):
        state = ok if bib.get("path_exists") else "missing"
        default = " (default)" if bib.get("default") else ""
        lines.append(f"bib {bib.get('name')}{default}: {state} ({bib.get('path')})")

    ts_url = result.get("translation_server_url")
    if ts_url:
        state = ok if result.get("translation_server_reachable") else bad
        lines.append(f"translation-server: {state} ({ts_url})")
        probe_error = result.get("translation_probe_error")
        if probe_error:
            lines.append(f"  - {probe_error}")

    s2 = result.get("semantic_scholar") or {}
    if s2:
        # A broken key command is a failure even when the API itself answers —
        # otherwise the header reads "ok" directly above the error explaining
        # that the configured key could not be obtained.
        state = ok if s2.get("reachable") and not s2.get("key_error") else bad
        configured = s2.get("configured", "unknown")
        lines.append(f"semantic scholar: {state} (key: {configured})")
        if s2.get("reachable") and configured == "not configured":
            # `doctor` probes the DOI-lookup endpoint; `check` uses title
            # search, and S2 rate-limits an anonymous caller per endpoint. Both
            # tools were honest and the pair read as a contradiction — say which
            # question this "ok" answered.
            lines.append(
                "  - reachable without a key, on the shared anonymous quota: "
                "other commands may still hit its rate-limit (HTTP 429). "
                "Set semantic_scholar_api_key for a quota of your own."
            )
        if s2.get("key_error"):
            lines.append(f"  - semantic_scholar_api_key_cmd failed: {s2['key_error']}")
        if s2.get("probe_error"):
            lines.append(f"  - {s2['probe_error']}")

    credentials = result.get("credentials") or {}
    for name, status in sorted(credentials.items()):
        lines.append(f"credential {name}: {status}")

    warning = result.get("config_permissions_warning")
    if warning:
        lines.append(f"warning: {warning}")
    return lines
