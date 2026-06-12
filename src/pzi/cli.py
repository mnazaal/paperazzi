"""CLI entrypoints for pzi."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, TextIO, TypeAlias, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pzi import cli_version_text, setup_service
from pzi.bib_service import bib_stats, delete_entry, list_bibs, set_default_bib
from pzi.capture_core import capture_to_bib
from pzi.capture_models import AuthHints, CaptureInput, CaptureOptions, PdfCandidate, load_page_artifact
from pzi.config import default_config_path
from pzi.doctor_service import doctor_check
from pzi.http_security import (
    DEFAULT_MAX_BODY_BYTES,
    HttpSecurityConfig,
    build_http_security_config,
    loopback_bind_host,
)
from pzi.pdf_service import attach_pdf, retry_pdf
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib
from pzi.tag_service import add_tags, list_tags, parse_tag_csv, remove_tags
from pzi.update_service import update_bib

# ---------------------------------------------------------------------------
# CLI parser (merged from cli_parser.py)
# ---------------------------------------------------------------------------

class _PziHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Suppress the auto-generated subparser "positional arguments" section."""

    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            return ""
        return super()._format_action(action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pzi",
        usage="pzi <command> [<args>]",
        description="Capture papers into local BibTeX libraries from DOI, URL, or PDF.",
        epilog=(
            "pzi add <doi|url|pdf>              Capture a paper\n"
            "pzi pdf retry <citekey>            Retry PDF download for an entry\n"
            "pzi pdf attach <citekey> <source>  Attach a PDF by URL or file path\n"
            "pzi tag add <citekey> <tag...>     Add tags to an entry\n"
            "pzi tag remove <citekey> <tag...>  Remove tags from an entry\n"
            "pzi tag list [citekey]             List tags for an entry or all tags\n"
            "pzi search --query <q> ...         Search BibTeX entries\n"
            "pzi update                         Conservatively fill missing metadata\n"
            "pzi promote                        Find published versions of preprints\n"
            "pzi list                           List configured BibTeX libraries\n"
            "pzi entries                        List all BibTeX entries\n"
            "pzi export                         Export library to CSV/JSON/RIS/BibTeX\
"
            "pzi import <file.bib>               Import entries from a BibTeX file\
"
            "pzi detail <citekey>               Show full record for an entry\n"
            "pzi set-default <name>             Set default BibTeX library\n"
            "pzi delete <citekey>               Delete a BibTeX entry by citekey\n"
            "pzi doctor                         Check configuration and service health\n"
            "pzi server                         Start HTTP API server\n"
            "pzi init                           Create or overwrite configuration\n"
            "pzi services up|down|status        Manage translation-server process\n"
            "pzi browser install [browser]      Install Playwright browser binary\n"
            "pzi config validate                Validate configuration file\n"
            "pzi bib-stats                      Show stats for a BibTeX library\n"
            "pzi clean                          Check library for integrity issues\n"
            "pzi dedupe                         Find duplicate entries in a library\n"
            "pzi version                        Show pzi version\n"
            "\n"
            "Run 'pzi <command> --help' for detailed options."
        ),
        formatter_class=_PziHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=cli_version_text())
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    def add_single_target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target", help="configured library name/path or direct .bib path")

    def add_multi_target(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--target",
            nargs="+",
            help="one or more configured library names/paths or direct .bib paths",
        )

    add_parser = subparsers.add_parser(
        "add",
        help="Capture a paper by DOI, URL, or PDF path",
    )
    add_parser.add_argument("value")
    add_parser.add_argument("--citekey")
    add_parser.add_argument("--title")
    add_parser.add_argument("--year", type=int)
    add_parser.add_argument("--authors")
    add_parser.add_argument("--tags")
    add_parser.add_argument(
        "--metadata-json",
        help="merge record metadata from JSON file, or '-' for stdin",
    )
    add_parser.add_argument(
        "--cookie-file",
        help="read browser Cookie header text from file, or '-' for stdin",
    )
    add_parser.add_argument(
        "--pdf-candidate",
        action="append",
        default=[],
        help="candidate PDF URL/path to try; may be repeated",
    )
    add_parser.add_argument(
        "--page-html",
        help="read captured page HTML from file, or '-' for stdin (reserved for page processors)",
    )
    add_parser.add_argument(
        "--page-metadata-cmd",
        help="external command that reads page JSON on stdin and writes metadata JSON",
    )
    add_parser.add_argument("--config")
    add_single_target(add_parser)
    add_parser.add_argument("--dry-run", action="store_true")
    add_parser.add_argument("--verbose", action="store_true")
    add_parser.add_argument("--json", action="store_true", help="write result as JSON")

    pdf_parser = subparsers.add_parser(
        "pdf",
        help="Manage PDF attachments",
    )
    pdf_sub = pdf_parser.add_subparsers(dest="pdf_command", required=True)
    pdf_retry = pdf_sub.add_parser("retry", help="Retry PDF download for an entry")
    pdf_retry.add_argument("citekey", nargs="?")
    pdf_retry.add_argument("--config")
    add_single_target(pdf_retry)
    pdf_retry.add_argument(
        "--failed-only",
        action="store_true",
        help="retry PDF for all entries with no local PDF (ignores citekey argument)",
    )
    pdf_attach = pdf_sub.add_parser("attach", help="Attach a PDF by URL or file path")
    pdf_attach.add_argument("citekey")
    pdf_attach.add_argument("source")
    pdf_attach.add_argument("--config")
    add_single_target(pdf_attach)

    tag_parser = subparsers.add_parser(
        "tag",
        help="Manage tags on BibTeX entries",
    )
    tag_sub = tag_parser.add_subparsers(dest="tag_command", required=True)
    tag_add_p = tag_sub.add_parser("add", help="Add tags to an entry")
    tag_add_p.add_argument("citekey")
    tag_add_p.add_argument("tags", nargs="+")
    tag_add_p.add_argument("--config")
    add_single_target(tag_add_p)
    tag_add_p.add_argument("--dry-run", action="store_true")
    tag_rm_p = tag_sub.add_parser("remove", help="Remove tags from an entry")
    tag_rm_p.add_argument("citekey")
    tag_rm_p.add_argument("tags", nargs="+")
    tag_rm_p.add_argument("--config")
    add_single_target(tag_rm_p)
    tag_rm_p.add_argument("--dry-run", action="store_true")
    tag_list_p = tag_sub.add_parser("list", help="List tags for an entry or all tags")
    tag_list_p.add_argument("citekey", nargs="?")
    tag_list_p.add_argument("--config")
    add_single_target(tag_list_p)

    search_parser = subparsers.add_parser(
        "search",
        help="Search BibTeX entries by query, author, year, or tag",
    )
    search_parser.add_argument("--query")
    search_parser.add_argument("--author")
    search_parser.add_argument("--year", type=int)
    search_parser.add_argument("--tag")
    search_parser.add_argument("--config")
    add_multi_target(search_parser)

    update_parser = subparsers.add_parser(
        "update",
        help="conservatively fill missing metadata; does not promote preprints",
        description=(
            "Conservatively enrich entries by filling missing metadata only. "
            "This does not replace preprints with published versions; use "
            "'pzi promote' for preprint→published promotion."
        ),
    )
    update_parser.add_argument("--config")
    add_multi_target(update_parser)
    update_parser.add_argument("--dry-run", action="store_true")
    update_parser.add_argument("--verbose", action="store_true")

    promote_parser = subparsers.add_parser(
        "promote",
        help="find published versions of preprints and update/create entries",
        description=(
            "Find published versions of preprint entries. By default, keeps the "
            "preprint and creates a published entry; with --replace, updates the "
            "preprint entry in place."
        ),
    )
    promote_parser.add_argument("--config")
    add_multi_target(promote_parser)
    promote_parser.add_argument("--dry-run", action="store_true")
    promote_parser.add_argument("--verbose", action="store_true")
    promote_parser.add_argument(
        "--replace",
        action="store_true",
        help="update the preprint entry in place instead of keeping both versions",
    )

    list_parser = subparsers.add_parser("list", help="list configured BibTeX libraries")
    list_parser.add_argument("--config")

    set_default_parser = subparsers.add_parser("set-default", help="set default BibTeX library")
    set_default_parser.add_argument("name")
    set_default_parser.add_argument("--config")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check configuration and service health",
    )
    doctor_parser.add_argument("--config")

    server_parser = subparsers.add_parser(
        "server",
        help="Start HTTP API server for browser extension",
    )
    server_parser.add_argument("--config")
    server_parser.add_argument("--host")
    server_parser.add_argument("--port", type=int)
    server_parser.add_argument(
        "--stop-after",
        type=int,
        metavar="MINUTES",
        help="auto-stop translation-server after N idle minutes",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Create or overwrite pzi configuration",
    )
    init_parser.add_argument("--config")
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )
    init_parser.add_argument(
        "--setup",
        action="store_true",
        help="write config, install translation-server, and set up browser fallback",
    )
    init_parser.add_argument(
        "--with-browser",
        action="store_true",
        help="configure and install browser fallback",
    )
    init_parser.add_argument(
        "--bib", default="~/bibs/main.bib", help="default BibTeX file path for --setup"
    )
    init_parser.add_argument(
        "--papers-dir",
        help="PDF storage directory for --setup; defaults to <bib-dir>/papers",
    )
    init_parser.add_argument("--name", default="main", help="default bib name for --setup")
    init_parser.add_argument(
        "--browser",
        default="chromium",
        choices=["chromium", "firefox"],
        help="browser for PDF fallback (default: chromium)",
    )

    services_parser = subparsers.add_parser(
        "services",
        help="Manage translation-server process",
    )
    services_sub = services_parser.add_subparsers(
        dest="services_command", required=True
    )
    for command, cmd_help in [
        ("up", "Start translation-server"),
        ("down", "Stop translation-server"),
        ("status", "Show translation-server status"),
        ("update", "Reinstall translation-server with latest pinned versions"),
    ]:
        p = services_sub.add_parser(command, help=cmd_help)
        p.add_argument("--config")

    browser_parser = subparsers.add_parser(
        "browser",
        help="Manage Playwright browser binaries",
    )
    browser_sub = browser_parser.add_subparsers(dest="browser_command", required=True)
    browser_install = browser_sub.add_parser("install", help="Install Playwright browser binary")
    browser_install.add_argument("browser", nargs="?", default="chromium")
    browser_install.add_argument("--config")

    _ = subparsers.add_parser("version", help="show pzi version")

    _config_parser = subparsers.add_parser(
        "config",
        help="Validate or inspect pzi configuration",
    )
    _config_sub = _config_parser.add_subparsers(dest="config_command", required=True)
    _ = _config_sub.add_parser("validate", help="validate config file")
    _.add_argument("--config")

    bib_stats_parser = subparsers.add_parser("bib-stats", help="show stats for BibTeX library")
    bib_stats_parser.add_argument("--config")
    add_single_target(bib_stats_parser)

    delete_parser = subparsers.add_parser("delete", help="delete a BibTeX entry by citekey")
    delete_parser.add_argument("citekey", help="citekey of the entry to delete")
    delete_parser.add_argument("--config")
    add_single_target(delete_parser)
    delete_parser.add_argument("--dry-run", action="store_true", help="preview without deleting")
    delete_parser.add_argument(
        "--force", action="store_true", help="skip confirmation prompt"
    )

    entries_parser = subparsers.add_parser(
        "entries",
        help="list all entries in a BibTeX library",
    )
    entries_parser.add_argument("--config")
    entries_parser.add_argument("--offset", type=int, default=0, help="pagination offset (default: 0)")
    entries_parser.add_argument("--limit", type=int, default=50, help="entries per page (default: 50)")
    entries_parser.add_argument(
        "--sort", default="citekey", choices=["citekey", "title", "year", "author"],
        help="sort field (default: citekey)",
    )
    add_single_target(entries_parser)

    detail_parser = subparsers.add_parser(
        "detail",
        help="show full record detail for a single entry",
    )
    detail_parser.add_argument("citekey", help="citekey of the entry")
    detail_parser.add_argument("--config")
    detail_parser.add_argument("--json", action="store_true", help="output full record as JSON")
    add_single_target(detail_parser)

    clean_parser = subparsers.add_parser(
        "clean",
        help="check and clean a BibTeX library for integrity issues",
    )
    clean_parser.add_argument("--config")
    add_single_target(clean_parser)
    clean_parser.add_argument(
        "--dry-run", action="store_true", help="report issues without fixing"
    )
    clean_parser.add_argument(
        "--fix", action="store_true",
        help="apply fixes (move orphan PDFs, sort entries)"
    )

    dedupe_parser = subparsers.add_parser(
        "dedupe",
        help="find duplicate entries in a BibTeX library",
    )
    dedupe_parser.add_argument("--config")
    add_single_target(dedupe_parser)

    merge_parser = subparsers.add_parser(
        "merge",
        help="merge two BibTeX entries by citekey",
    )
    merge_parser.add_argument("citekey_a", help="source citekey (will be merged into citekey_b)")
    merge_parser.add_argument("citekey_b", help="target citekey (will receive merged fields)")
    merge_parser.add_argument("--config")
    add_single_target(merge_parser)
    merge_parser.add_argument("--dry-run", action="store_true", help="preview without merging")

    export_parser = subparsers.add_parser(
        "export",
        help="export BibTeX library to various formats",
    )
    export_parser.add_argument("--config")
    add_single_target(export_parser)
    export_parser.add_argument(
        "--format", default="bibtex", choices=["bibtex", "csv", "json", "ris"],
        help="output format (default: bibtex)",
    )
    export_parser.add_argument(
        "-o", "--output", help="output file path (default: stdout)",
    )

    import_parser = subparsers.add_parser(
        "import",
        help="import entries from a BibTeX file into your library",
    )
    import_parser.add_argument("source", help="path to source .bib file")
    import_parser.add_argument("--config")
    add_single_target(import_parser)
    import_parser.add_argument(
        "--dry-run", action="store_true", help="preview without importing"
    )
    import_parser.add_argument(
        "--force-new", action="store_true",
        help="import as new entries even if duplicates are found"
    )

    reindex_parser = subparsers.add_parser(
        "reindex",
        help="regenerate citekeys and fix file references",
    )
    reindex_parser.add_argument("--config")
    add_single_target(reindex_parser)
    reindex_parser.add_argument("--dry-run", action="store_true", help="preview changes without applying")
    reindex_parser.add_argument(
        "--force", action="store_true", help="skip confirmation prompt"
    )

    watch_parser = subparsers.add_parser(
        "watch", help="watch a directory and auto-import new .pdf/.bib files"
    )
    watch_parser.add_argument("directory", help="directory to watch")
    watch_parser.add_argument("--config")
    add_single_target(watch_parser)
    watch_parser.add_argument(
        "--interval", type=int, default=5,
        help="polling interval in seconds (default: 5)",
    )
    watch_parser.add_argument(
        "--recursive", action="store_true",
        help="scan sub-directories recursively",
    )
    watch_parser.add_argument(
        "--dry-run", action="store_true", help="preview without importing"
    )
    watch_parser.add_argument(
        "--max-runtime", type=int, default=None,
        help="stop after N seconds (default: run until interrupted)",
    )

    return parser


def load_text_arg(path: str, *, stdin_text: str | None = None) -> str:
    """Load text from a path or stdin marker. Pure boundary helper for CLI capture."""
    if path == "-":
        return sys.stdin.read() if stdin_text is None else stdin_text
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_add_metadata_json(
    path: str,
    *,
    stdin_text: str | None = None,
) -> dict[str, object]:
    """Load record metadata JSON for `pzi add --metadata-json`."""
    payload = json.loads(load_text_arg(path, stdin_text=stdin_text))
    if not isinstance(payload, dict):
        raise ValueError("metadata JSON must be an object")
    return dict(payload)


def build_record_overrides_from_add_args(args: argparse.Namespace) -> dict[str, object]:
    record: dict[str, object] = {}

    if getattr(args, "metadata_json", None):
        record.update(load_add_metadata_json(args.metadata_json))

    if args.citekey is not None:
        record["citekey"] = args.citekey
    if args.title is not None:
        record["title"] = args.title
    if args.year is not None:
        record["year"] = args.year
    if args.authors:
        record["authors"] = [
            part.strip() for part in args.authors.split(";") if part.strip()
        ]
    if args.tags:
        record["tags"] = parse_tag_csv(args.tags)

    return record


def build_capture_input_from_add_args(
    args: argparse.Namespace,
    *,
    bib_selector: str | None,
) -> CaptureInput:
    """Build pure capture input from parsed `pzi add` args."""
    pdf_candidates = tuple(
        PdfCandidate(value=value, source="cli")
        for value in getattr(args, "pdf_candidate", [])
    )
    cookies = None
    if getattr(args, "cookie_file", None):
        cookies = load_text_arg(args.cookie_file).strip()
    page_artifact = None
    if getattr(args, "page_html", None):
        page_artifact = load_page_artifact(args.page_html)
    return CaptureInput(
        value=args.value,
        record_overrides=build_record_overrides_from_add_args(args),
        bib_selector=bib_selector,
        pdf_candidates=pdf_candidates,
        page_artifact=page_artifact,
        auth_hints=AuthHints(cookies=cookies),
    )


def build_capture_options_from_add_args(
    args: argparse.Namespace,
    *,
    config: Mapping[str, object] | None,
) -> CaptureOptions:
    """Build pure capture run options from parsed `pzi add` args and config."""
    cfg = config or {}
    page_metadata_cmd = getattr(args, "page_metadata_cmd", None) or cfg.get(
        "page_metadata_cmd"
    )
    timeout = cfg.get("page_metadata_timeout_seconds", 5)
    return CaptureOptions(
        dry_run=args.dry_run,
        force_new=getattr(args, "force_new", False),
        page_metadata_cmd=(
            page_metadata_cmd if isinstance(page_metadata_cmd, str) and page_metadata_cmd.strip() else None
        ),
        page_metadata_timeout_seconds=int(timeout) if isinstance(timeout, int) else 5,
    )


# ---------------------------------------------------------------------------
# CLI render helpers (merged from cli_render.py)
# ---------------------------------------------------------------------------

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
        lines.append(f"{match['citekey']}\t{year}\t{title}\t[{fields}]")
    return lines or ["no matches"]


def _render_bib_list(result: Mapping[str, Any]) -> list[str]:
    lines = []
    for bib in result["bibs"]:
        marker = " (default)" if bib["default"] else ""
        lines.append(f"{bib['name']}\t{bib['path']}{marker}")
    return lines


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
        )
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


def _render_watch_result(result: Mapping[str, Any]) -> list[str]:
    """Render watch result as human-readable lines."""
    lines: list[str] = [
        f"watch dir: {result['watch_dir']}",
        f"imported: {result['total']}",
    ]
    if result.get("error_count"):
        lines.append(f"errors: {result['error_count']}")
    for item in result.get("imported", [])[:10]:
        citekey = item.get("citekey") or "?"
        ftype = item.get("type", "?")
        lines.append(f"  {ftype}: {citekey} ({item['file']})")
    for err in result.get("errors", [])[:10]:
        lines.append(f"  ERROR: {err['file']} — {err['error']}")
    return lines


def _render_delete_success(result: Mapping[str, Any]) -> str:
    """Render delete result as a single status line."""
    prefix = "DRY RUN: " if result["dry_run"] else ""
    msg = result["message"]
    pdf = f" (PDF at {result['pdf_path']})" if result.get("pdf_path") else ""
    return f"{prefix}{msg}{pdf}"


# ---------------------------------------------------------------------------
# CLI entrypoints
# ---------------------------------------------------------------------------

def run_cli(
    argv: Sequence[str],
    *,
    home_dir: str | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    parser = build_parser()
    try:
        import argcomplete  # noqa: F811

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    out = stdout or sys.stdout
    err = stderr or sys.stderr

    if not argv:
        parser.print_help(file=out)
        return 0

    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exc:
        # argparse raises SystemExit(2) on unknown subcommand or bad args.
        # Let the original error message stand — do not print extra help.
        return exc.code if isinstance(exc.code, int) else 1

    effective_home = home_dir or os.path.expanduser("~")
    config_path: str = getattr(args, "config", None) or default_config_path(effective_home)

    if args.command is None:
        parser.print_help(file=out)
        return 0

    _cfg = dict(home_dir=effective_home, config_path=config_path)
    _bib_selector: str | None = getattr(args, "target", None)

    _dispatch: dict[str, Callable[[], int]] = {
        "add": lambda: _run_add(
            args, **_cfg,
            stdout=out, stderr=err, bib_selector=_bib_selector,
            fetch_web=fetch_web, fetch_search=fetch_search,
        ),
        "bib-stats": lambda: _run_bib_stats(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "config": lambda: _run_config(
            args, **_cfg, stdout=out, stderr=err,
        ),
        "delete": lambda: _run_delete(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "detail": lambda: _run_detail(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "doctor": lambda: _run_doctor(
            home_dir=effective_home, config_path=config_path, stdout=out, stderr=err,
        ),
        "entries": lambda: _run_entries(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "export": lambda: _run_export(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "import": lambda: _run_import(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "init": lambda: _run_init(
            args, config_path=config_path, stdout=out, stderr=err,
        ),
        "merge": lambda: _run_merge(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "pdf": lambda: _run_pdf_retry(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "promote": lambda: _run_promote(
            args, **_cfg, stdout=out, stderr=err,
        ),
        "reindex": lambda: _run_reindex(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "search": lambda: _run_search(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "server": lambda: _run_server(
            args, **_cfg, stdout=out, stderr=err,
        ),
        "services": lambda: _run_services(
            args, config_path=config_path, stdout=out, stderr=err,
        ),
        "tag": lambda: _run_tag(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "update": lambda: _run_update(
            args, **_cfg, stdout=out, stderr=err,
        ),
        "watch": lambda: _run_watch(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "browser": lambda: _run_browser(
            args, stdout=out, stderr=err,
        ),
        "clean": lambda: _run_clean(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "dedupe": lambda: _run_dedupe(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
    }

    if args.command in _dispatch:
        return _dispatch[args.command]()

    if args.command == "list":
        result = list_bibs(config_path=config_path, home_dir=effective_home)
        if result["status"] == "ok":
            _print_lines(_render_bib_list(result), out)
            return 0
        _print_lines(_error_lines("failed to list bibs", result["errors"]), err)
        return 1

    if args.command == "set-default":
        result = set_default_bib(config_path=config_path, home_dir=effective_home, name=args.name)
        if result["status"] == "ok":
            print(result["message"], file=out)
            return 0
        _print_lines(_error_lines(result["message"], result["errors"]), err)
        return 1

    if args.command == "version":
        print(cli_version_text(), file=out)
        return 0

    print(f"unknown command: {args.command}", file=err)
    return 2


def _run_init(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    import importlib.resources
    from pathlib import Path

    dest = Path(config_path)
    if dest.exists() and not args.force:
        print(f"config already exists: {dest} (use --force to overwrite)", file=stderr)
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    setup_mode = args.setup or args.with_browser
    with_browser = setup_mode

    if setup_mode:
        content = setup_service.render_config(
            bib_name=args.name,
            bib_path=args.bib,
            papers_dir=args.papers_dir,
            with_browser=with_browser,
            browser=args.browser if with_browser else "chromium",
        )
    else:
        template = importlib.resources.files("pzi").joinpath("config.template.toml")
        with importlib.resources.as_file(template) as src:
            content = Path(src).read_text()
    dest.write_text(content)
    print(f"created {dest}", file=stdout)

    if setup_mode:
        # Pre-cache Node.js and translation-server if possible (non-interactive)
        from pzi.ts_backend import ensure_node, ensure_translation_server

        data_home = Path(os.path.expanduser("~/.local/share/pzi"))
        node = ensure_node(data_home, interactive=False, stdout=stdout, stderr=stderr)
        if node is not None:
            ensure_translation_server(data_home, node, stdout=stdout, stderr=stderr)

    if with_browser:
        code = setup_service.install_playwright_browser(
            args.browser, stdout=stdout, stderr=stderr
        )
        if code != 0:
            print("browser install failed", file=stderr)
            return code
    return 0


def _run_services(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    from pathlib import Path

    from pzi.config import load_config_file
    from pzi.ts_backend import (
        auto_start_ts,
        ensure_node,
        is_ts_reachable,
        stop_ts,
    )
    from pzi.ts_backend import (
        ensure_translation_server as ensure_ts,
    )

    home_dir = os.path.expanduser("~")
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    if config is None:
        print("failed to load config", file=stderr)
        return 1

    ts_url = config.get("translation_server_url")
    if not isinstance(ts_url, str) or not ts_url:
        print("translation_server_url not configured", file=stderr)
        return 1

    data_home = Path(config.get("pzi_data_home", "~/.local/share/pzi")).expanduser()
    pid_file = data_home / "ts.pid"
    action = args.services_command

    if action == "up":
        return 0 if auto_start_ts(config, config_path, str(data_home),
                                  interactive=True,
                                  stdout=stdout, stderr=stderr) else 1
    elif action == "down":
        if not is_ts_reachable(ts_url):
            print("translation-server is not running", file=stdout)
            return 0
        ok = stop_ts(pid_file)
        print(
            "translation-server stopped" if ok else "failed to stop translation-server",
            file=stdout,
        )
        return 0 if ok else 1
    elif action == "status":
        if is_ts_reachable(ts_url):
            print(f"translation-server is running at {ts_url}", file=stdout)
        else:
            print("translation-server is not running", file=stdout)
        return 0
    elif action == "update":
        # Re-download/reinstall translation-server
        print("reinstalling translation-server …", file=stdout)
        node = ensure_node(data_home, interactive=True, stdout=stdout, stderr=stderr)
        if node is None:
            return 1
        # Remove existing install to trigger full re-clone
        ts_dir = data_home / "ts"
        if ts_dir.exists():
            if is_ts_reachable(ts_url):
                print("stopping translation-server …", file=stdout)
                stop_ts(pid_file)
            import shutil
            shutil.rmtree(ts_dir, ignore_errors=True)
        result = ensure_ts(data_home, node, stdout=stdout, stderr=stderr)
        if result is None:
            return 1
        print("translation-server reinstalled. Run `pzi services up` to start.", file=stdout)
        return 0
    else:
        print(f"unknown services command: {action}", file=stderr)
        return 2


def _run_browser(args, *, stdout: TextIO, stderr: TextIO) -> int:
    if args.browser_command == "install":
        return setup_service.install_playwright_browser(
            args.browser,
            stdout=stdout,
            stderr=stderr,
        )
    print(f"unknown browser command: {args.browser_command}", file=stderr)
    return 2


def _run_config(args, *, home_dir, config_path, stdout, stderr) -> int:
    if args.config_command == "validate":
        from pzi.config import load_config_file
        result = load_config_file(config_path, home_dir=home_dir)
        if result["config"] is not None:
            print(f"config valid: {result['path']}", file=stdout)
            return 0
        _print_lines(_error_lines("config invalid", result["errors"]), stderr)
        return 1
    print(f"unknown config command: {args.config_command}", file=stderr)
    return 2


def _run_bib_stats(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    result = bib_stats(bib_path=target["path"], papers_dir=target["papers_dir"])
    if result["status"] == "ok":
        _print_lines(_render_bib_stats(result), stdout)
        return 0
    _print_lines(_error_lines("bib-stats failed", result["errors"]), stderr)
    return 1


def _run_clean(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.clean_service import clean_library, validate_library
    from pzi.config import load_config_file, resolve_library_target

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    if args.fix:
        result = clean_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
            dry_run=args.dry_run,
        )
    else:
        result = validate_library(
            bib_path=target["path"], papers_dir=target["papers_dir"],
        )

    if result["status"] != "ok":
        _print_lines(_error_lines("clean failed", [result.get("message", "")]), stderr)
        return 1

    _print_lines(_render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return 0 if not result.get("issues") else 1


def _run_dedupe(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target
    from pzi.dedupe_service import find_duplicates

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    result = find_duplicates(bib_path=target["path"])
    _print_lines(_render_dedupe_result(result), stdout)
    return 0 if result.get("total_clusters", 0) == 0 else 1


def _run_merge(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target
    from pzi.dedupe_service import merge_duplicates

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    result = merge_duplicates(
        bib_path=target["path"],
        citekey_a=args.citekey_a,
        citekey_b=args.citekey_b,
        dry_run=getattr(args, "dry_run", False),
    )
    if result["status"] != "ok":
        _print_lines(_error_lines(result["message"], []), stderr)
        return 1
    print(result["message"], file=stdout)
    return 0


def _run_reindex(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target
    from pzi.reindex_service import reindex_library

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    if not args.dry_run and not args.force:
        print("Reindex will regenerate citekeys and rename PDFs.", file=stdout)
        print("Run with --dry-run to preview, or --force to apply.", file=stdout)
        return 0

    result = reindex_library(
        bib_path=target["path"],
        papers_dir=target["papers_dir"],
        citekey_format=cfg["config"].get("citekey_format"),
        pdf_filename_format=cfg["config"].get("pdf_filename_format"),
        dry_run=args.dry_run,
    )

    if result["status"] != "ok":
        _print_lines(_error_lines("reindex failed", result.get("errors", [])), stderr)
        return 1

    _print_lines(_render_reindex_result(result, dry_run=args.dry_run), stdout)
    return 0 if not result.get("errors") else 1


def _run_watch(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.watch_service import watch_directory

    print(f"watching {args.directory} (poll every {args.interval}s) …", file=stdout)
    print("press Ctrl+C to stop", file=stdout)
    stdout.flush()

    result = watch_directory(
        watch_dir=args.directory,
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        poll_interval=args.interval,
        recursive=args.recursive,
        max_runtime=args.max_runtime,
        dry_run=args.dry_run,
    )

    if result["status"] != "ok":
        _print_lines(_error_lines("watch failed", [result.get("message", "")]), stderr)
        return 1

    _print_lines(_render_watch_result(result), stdout)
    return 0 if not result.get("error_count") else 1


def _run_export(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target
    from pzi.export_service import export_bibtex, export_csv, export_json, export_ris

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    fmt = args.format
    exporters = {
        "bibtex": export_bibtex,
        "csv": export_csv,
        "json": export_json,
        "ris": export_ris,
    }
    result = exporters[fmt](bib_path=target["path"])

    if result["status"] != "ok":
        _print_lines(_error_lines("export failed", result.get("errors", [])), stderr)
        return 1

    content = result["content"]
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(content, encoding="utf-8")
        print(f"exported {result['total_entries']} entries to {args.output}", file=stdout)
    else:
        print(content, file=stdout)
    return 0


def _run_import(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.import_service import import_from_bibtex

    if not getattr(args, "source", None):
        print("error: source .bib file required", file=stderr)
        return 2

    source = args.source
    if not os.path.exists(source):
        print(f"error: source file not found: {source}", file=stderr)
        return 1

    result = import_from_bibtex(
        config_path=config_path,
        home_dir=home_dir,
        source_path=source,
        bib_selector=bib_selector,
        dry_run=getattr(args, "dry_run", False),
        force_new=getattr(args, "force_new", False),
    )

    if result["status"] == "error":
        _print_lines(_error_lines("import failed", result.get("errors", [])), stderr)
        return 1

    prefix = "DRY RUN: " if getattr(args, "dry_run", False) else ""
    print(f"{prefix}imported {result['imported']}/{result['total_source']} entries", file=stdout)
    if result["skipped_duplicates"]:
        print(f"{prefix}skipped {result['skipped_duplicates']} duplicates", file=stdout)
    if result["skipped_errors"]:
        print(f"{prefix}{result['skipped_errors']} errors", file=stdout)

    for r in result.get("results", []):
        status_mark = "✓" if r["status"] in ("imported", "would_import") else "✗"
        print(f"  {status_mark} {r['citekey']}: {r['status']}", file=stdout)

    if result.get("errors"):
        for err in result["errors"]:
            print(f"  ! {err}", file=stderr)

    return 0 if result["skipped_errors"] == 0 else 1


def _run_delete(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.config import load_config_file, resolve_library_target

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is None:
        _print_lines(_error_lines("failed to load config", cfg["errors"]), stderr)
        return 1

    target = resolve_library_target(
        cfg["config"]["bibs"], bib_selector, home_dir=home_dir,
    )
    if target is None:
        _print_lines(_error_lines("bib not found", []), stderr)
        return 1

    if not args.force and not args.dry_run:
        # Safety confirmation for destructive operation
        print(
            f"Delete entry '{args.citekey}' from {target['path']}? [y/N] ",
            end="",
            file=stderr,
        )
        response = sys.stdin.readline().strip().lower()
        if response not in ("y", "yes"):
            print("cancelled", file=stdout)
            return 0

    result = delete_entry(
        bib_path=target["path"],
        citekey=args.citekey,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        print(_render_delete_success(result), file=stdout)
        backup = result.get("backup_path")
        if isinstance(backup, str):
            print(f"backup saved to {backup}", file=stderr)
        return 0
    return _render_errors(result["message"], result["errors"], stderr)


def main() -> int:
    return run_cli(sys.argv[1:])


def _run_entries(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.bib_service import list_entries

    result = list_entries(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        offset=max(0, args.offset),
        limit=max(1, min(args.limit, 500)),
        sort=args.sort,
    )
    if result["status"] == "ok":
        items = result["items"]
        if not items:
            print("(no entries)", file=stdout)
            return 0
        for item in items:
            ck = item["citekey"]
            title = item.get("title", "") or ""
            year_str = str(item["year"]) if item.get("year") else ""
            authors = item.get("authors", "")
            pdf_marker = " [PDF]" if item.get("has_pdf") else ""
            line = f"{ck}\t{year_str}\t{title}"
            if authors:
                line += f"\t{authors}"
            line += pdf_marker
            print(line, file=stdout)
        total = result["total"]
        offset = result["offset"]
        limit = result["limit"]
        shown = min(len(items), limit)
        print(
            f"\n{offset + 1}-{offset + shown} of {total} entries "
            f"(bib: {result['bib_name']}, sort: {result['sort']})",
            file=stdout,
        )
        return 0
    _print_lines(_error_lines("failed to list entries", result["errors"]), stderr)
    return 1


def _run_detail(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.bib_service import entry_detail

    result = entry_detail(
        config_path=config_path,
        home_dir=home_dir,
        citekey=args.citekey,
        bib_selector=bib_selector,
    )
    if result["status"] == "ok":
        record = result["record"]
        if args.json:
            print(json.dumps(record, indent=2, default=str), file=stdout)
            return 0
        # Human-readable format
        print(f"citekey: {record.get('citekey', '')}", file=stdout)
        print(f"title: {record.get('title', '')}", file=stdout)
        year = record.get("year")
        if year:
            print(f"year: {year}", file=stdout)
        authors = record.get("authors")
        if isinstance(authors, list) and authors:
            names = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in authors if isinstance(a, dict)
            ]
            print(f"authors: {', '.join(names)}", file=stdout)
        for key in ("doi", "arxiv_id", "url", "entry_type", "journal"):
            val = record.get(key)
            if val:
                print(f"{key}: {val}", file=stdout)
        pdf = record.get("local_pdf_path")
        if pdf:
            print(f"pdf: {pdf}", file=stdout)
        tags = record.get("tags")
        if isinstance(tags, list) and tags:
            print(f"tags: {', '.join(str(t) for t in tags)}", file=stdout)
        abstract = record.get("abstract")
        if isinstance(abstract, str) and abstract.strip():
            print(f"\nabstract:\n{abstract.strip()}", file=stdout)
        return 0
    _print_lines(_error_lines(result["message"], result["errors"]), stderr)
    return 1


def _run_add(
    args: argparse.Namespace,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
) -> int:
    from pzi.config import load_config_file

    cfg = load_config_file(config_path, home_dir=home_dir)
    if cfg["config"] is not None and fetch_web is None and fetch_search is None:
        if not ensure_translation_server(cfg["config"], config_path, stdout, stderr):
            print(
                "translation server is not running — cannot add paper.\n"
                "  Run 'pzi services up' and wait for it to be ready, then retry.",
                file=stderr,
            )
            return 1

    service_kwargs = {}
    if fetch_web is not None:
        service_kwargs["fetch_web"] = fetch_web
    if fetch_search is not None:
        service_kwargs["fetch_search"] = fetch_search
    result = capture_to_bib(
        build_capture_input_from_add_args(args, bib_selector=bib_selector),
        build_capture_options_from_add_args(args, config=cfg.get("config")),
        config_path=config_path,
        home_dir=home_dir,
        service_kwargs=service_kwargs,
    )

    if result["status"] == "error":
        _print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        for warning in result["warnings"]:
            print(f"warning: {warning}", file=stderr)
        return 0

    print(_render_add_success(result), file=stdout)
    if args.dry_run and result.get("diff"):
        print(result["diff"], file=stdout, end="" if result["diff"].endswith("\n") else "\n")
    if args.verbose:
        _print_metadata_diagnostics(result, stdout)
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=stderr)
    return 0


def _run_pdf_retry(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if args.pdf_command == "attach":
        result = attach_pdf(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
            source=args.source,
        )
        if result["status"] == "ok":
            print(_render_pdf_success("attached", result), file=stdout)
            return 0
        _print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return 1

    if getattr(args, "failed_only", False):
        return _run_pdf_retry_failed_only(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            stdout=stdout,
            stderr=stderr,
        )

    if not args.citekey:
        print("error: citekey required (or use --failed-only for batch retry)", file=stderr)
        return 2

    result = retry_pdf(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
    )
    if result["status"] == "ok":
        print(_render_pdf_success("fetched", result), file=stdout)
        return 0
    _print_lines(_error_lines(result["message"], result["errors"]), stderr)
    return 1


def _run_pdf_retry_failed_only(
    *, config_path: str, home_dir: str, bib_selector: str | None,
    stdout: TextIO, stderr: TextIO,
) -> int:
    from pzi.pdf_service import retry_failed_pdfs

    result = retry_failed_pdfs(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if result["status"] == "error":
        _print_lines(_error_lines(result["message"], result["errors"]), stderr)
        return 1

    lines = [
        f"bib: {result['bib_name']}",
        f"succeeded: {result['succeeded']}/{result['total']}",
        f"skipped (already have PDF): {result['skipped_already_has_pdf']}",
        f"skipped (no PDF URL): {result['skipped_no_url']}",
    ]
    if result["failures"]:
        lines.append(f"failed: {len(result['failures'])}")
        for failure in result["failures"]:
            lines.append(f"  {failure['citekey']}: {failure['error']}")
    _print_lines(lines, stdout)
    return 0


def _run_tag(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if args.tag_command == "list":
        result = list_tags(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=bib_selector,
            citekey=args.citekey,
        )
        if result["status"] == "ok":
            for tag in result["tags"]:
                print(tag, file=stdout)
            return 0
        _print_lines(_error_lines("failed to list tags", result["errors"]), stderr)
        return 1

    flat_tags = [tag for raw in args.tags for tag in parse_tag_csv(raw)]

    fn = add_tags if args.tag_command == "add" else remove_tags
    result = fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        citekey=args.citekey,
        tags=flat_tags,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        print(_render_tag_mutation_success(result), file=stdout)
        return 0
    return _render_errors(result["message"], result["errors"], stderr)


def _render_errors(message: str, errors: list[str], stderr: TextIO) -> int:
    _print_lines(_error_lines(message, errors), stderr)
    return 1


def _print_lines(lines: Sequence[str], stream: TextIO) -> None:
    for line in lines:
        print(line, file=stream)


def _run_search(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    if not any((args.query, args.author, args.year, args.tag)):
        print("error: at least one of --query, --author, --year, --tag is required", file=stderr)
        return 1

    ok = True
    for target in _target_list(bib_selector):
        result = search_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            query=args.query,
            author=args.author,
            year=args.year,
            tag=args.tag,
        )
        if result["status"] == "ok":
            _print_lines(_render_search_matches(result), stdout)
        else:
            ok = False
            _print_lines(_error_lines("search failed", result["errors"]), stderr)
    return 0 if ok else 1


def _target_list(value: list[str] | None) -> list[str | None]:
    return list(value) if value else [None]


def _run_update(args, *, home_dir, config_path, stdout, stderr) -> int:
    ok = True
    for target in _target_list(args.target):
        result = update_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            dry_run=args.dry_run,
        )
        if result["status"] == "ok":
            _print_lines(_render_bib_update_items(result), stdout)
            if args.dry_run:
                _print_result_item_diffs(result, stdout)
            if args.verbose:
                _print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            _print_lines(_error_lines("update failed", result["errors"]), stderr)
    return 0 if ok else 1


def _run_promote(args, *, home_dir, config_path, stdout, stderr) -> int:
    ok = True
    for target in _target_list(args.target):
        result = promote_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=target,
            dry_run=args.dry_run,
            keep_preprint=not args.replace,
        )
        if result["status"] == "ok":
            _print_lines(_render_bib_promote_items(result), stdout)
            if args.dry_run:
                _print_result_item_diffs(result, stdout)
            if args.verbose:
                _print_metadata_diagnostics(result, stdout)
        else:
            ok = False
            _print_lines(_error_lines("promote failed", result["errors"]), stderr)
    return 0 if ok else 1


def _print_result_item_diffs(result: Mapping[str, object], stdout: TextIO) -> None:
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        diff = item.get("diff")
        if not isinstance(diff, str) or not diff:
            continue
        print(diff, file=stdout, end="" if diff.endswith("\n") else "\n")


def _print_metadata_diagnostics(result: Mapping[str, object], stdout: TextIO) -> None:
    lines = _metadata_diagnostic_lines(result)
    if not lines:
        return
    print("metadata diagnostics:", file=stdout)
    for line in lines:
        print(f"  {line}", file=stdout)


def _metadata_diagnostic_lines(result: Mapping[str, object]) -> list[str]:
    direct = result.get("metadata_diagnostics")
    if isinstance(direct, list):
        return [line for line in direct if isinstance(line, str)]
    lines: list[str] = []
    items = result.get("items")
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, Mapping):
            continue
        diagnostics = item.get("metadata_diagnostics")
        if not isinstance(diagnostics, list):
            continue
        lines.extend(line for line in diagnostics if isinstance(line, str))
    return lines


def _run_doctor(*, home_dir, config_path, stdout, stderr) -> int:
    result = doctor_check(config_path=config_path, home_dir=home_dir)
    print(json.dumps(result, indent=2, default=str), file=stdout)
    return 0 if result["config_ok"] else 1


def _run_server(args, *, home_dir, config_path, stdout, stderr) -> int:
    from pzi.config import load_config_file
    from pzi.http_api import run_server

    host = args.host
    port = args.port
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    plan = build_server_plan(host=host, port=port, config=config)
    if plan["status"] == "error":
        print(plan["message"], file=stderr)
        for error in cfg["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    host = plan["host"]
    port = plan["port"]

    if config is not None:
        if not ensure_translation_server(config, config_path, stdout, stderr):
            print(
                "warning: translation server is not running — "
                "capture requests will fail until it is ready",
                file=stderr,
            )

    stop_after = getattr(args, "stop_after", None)
    on_shutdown = None
    if stop_after is not None and config is not None:
        def _on_shutdown() -> None:
            print("stopping translation-server (idle timeout) …", file=stdout)
            from pathlib import Path

            from pzi.ts_backend import stop_ts as _stop_ts

            data_home = Path(config.get("pzi_data_home", "~/.local/share/pzi")).expanduser()
            _stop_ts(data_home / "ts.pid")
        on_shutdown = _on_shutdown

    print(f"serving on {host}:{port}", file=stdout)
    stdout.flush()
    run_server(
        config_path=config_path,
        home_dir=home_dir,
        host=host,
        port=port,
        security=plan["security"],
        idle_minutes=stop_after,
        on_shutdown=on_shutdown,
        browser_profile_path=config.get("browser_profile_path") if config else None,
        browser_engine=config.get("browser_engine", "chromium") if config else "chromium",
    )
    return 0


# ---------------------------------------------------------------------------
# Server plan types and function (merged from cli_server.py)
# ---------------------------------------------------------------------------


class ServerPlanError(TypedDict):
    status: Literal["error"]
    message: str


class ServerPlanOk(TypedDict):
    status: Literal["ok"]
    host: str
    port: int
    security: HttpSecurityConfig


ServerPlan: TypeAlias = ServerPlanOk | ServerPlanError


def build_server_plan(
    *,
    host: str | None,
    port: int | None,
    config: dict[str, Any] | None,
) -> ServerPlan:
    """Resolve server host/port/security without I/O."""
    if config is None and (host is None or port is None):
        return {"status": "error", "message": "failed to load config"}

    resolved_host = host
    resolved_port = port
    if config is not None:
        resolved_host = resolved_host or config["api_listen_host"]
        resolved_port = resolved_port or config["api_listen_port"]

    if resolved_host is None or resolved_port is None:
        return {"status": "error", "message": "failed to load config"}

    auth_token = config.get("api_auth_token") if config is not None else None
    if not auth_token and not loopback_bind_host(resolved_host):
        return {
            "status": "error",
            "message": (
                "refusing to serve unauthenticated API on a non-loopback host; "
                "set api_auth_token or bind to 127.0.0.1/localhost"
            ),
        }

    security = build_http_security_config(
        auth_token=auth_token,
        allowed_origins=config.get("api_allowed_origins") if config is not None else None,
        max_body_bytes=config.get("api_max_body_bytes", DEFAULT_MAX_BODY_BYTES)
        if config is not None
        else DEFAULT_MAX_BODY_BYTES,
        rate_limit_rpm=config.get("rate_limit_rpm", 60) if config is not None else 60,
    )
    return {
        "status": "ok",
        "host": resolved_host,
        "port": resolved_port,
        "security": security,
    }


# ---------------------------------------------------------------------------
# Translation server helpers
# ---------------------------------------------------------------------------


def ensure_translation_server(
    config: dict[str, object], config_path: str, stdout: TextIO, stderr: TextIO
) -> bool:
    """Ensure translation-server is reachable. Returns True if ready.

    Checks if already reachable first.  If not, delegates to
    ts_backend.auto_start_ts which handles Node.js bootstrap + subprocess
    start.
    """
    if os.environ.get("PZI_SKIP_AUTO_START"):
        return True

    ts_url = config.get("translation_server_url")
    if not isinstance(ts_url, str) or not ts_url:
        return True

    # Quick check: is it already reachable?
    try:
        req = Request(ts_url.rstrip("/"), method="GET")
        urlopen(req, timeout=2)
        return True
    except HTTPError:
        return True  # server responded even if not 2xx
    except (URLError, OSError, ValueError):
        pass

    from pzi.ts_backend import auto_start_ts as _auto_start

    data_home = config.get("pzi_data_home", os.path.expanduser("~/.local/share/pzi"))
    return _auto_start(config, config_path, str(data_home),
                       interactive=True, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    raise SystemExit(main())
