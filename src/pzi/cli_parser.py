"""CLI argument parser and input builders.

Pure boundary: defines argparse structure and builds typed input objects
from parsed args.  No service calls, no I/O beyond load_text_arg.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from pzi import cli_version_text
from pzi.capture_models import (
    AuthHints,
    CaptureInput,
    CaptureOptions,
    PdfCandidate,
    load_page_artifact,
)
from pzi.tag_service import parse_tag_csv

# ---------------------------------------------------------------------------
# HelpFormatter — suppress subparser "positional arguments" section
# ---------------------------------------------------------------------------


class _PziHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Suppress the auto-generated subparser "positional arguments" section."""

    def _format_action(self, action: argparse.Action) -> str:
        if isinstance(action, argparse._SubParsersAction):
            return ""
        return super()._format_action(action)


# ---------------------------------------------------------------------------
# Parser builder
# ---------------------------------------------------------------------------


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
            "pzi export                         Export library to CSV/JSON/RIS/BibTeX\n"
            "pzi import <file.bib>               Import entries from a BibTeX file\n"
            "pzi detail <citekey>               Show full record for an entry\n"
            "pzi set-default <name>             Set default BibTeX library\n"
            "pzi delete <citekey>               Delete a BibTeX entry by citekey\n"
            "pzi doctor                         Check configuration and service health\n"
            "pzi server                         Start HTTP API + translation-server\n"
            "pzi init                           Create or overwrite configuration\n"
            "pzi services status|update         Inspect or reinstall translation-server\n"
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

    # ── add ─────────────────────────────────────────────────────────────
    add_parser = subparsers.add_parser("add", help="Capture a paper by DOI, URL, or PDF path")
    add_parser.add_argument("value")
    add_parser.add_argument("--citekey")
    add_parser.add_argument("--title")
    add_parser.add_argument("--year", type=int)
    add_parser.add_argument("--authors")
    add_parser.add_argument("--tags")
    add_parser.add_argument(
        "--metadata-json", help="merge record metadata from JSON file, or '-' for stdin"
    )
    add_parser.add_argument(
        "--cookie-file", help="read browser Cookie header text from file, or '-' for stdin"
    )
    add_parser.add_argument(
        "--pdf-candidate", action="append", default=[],
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

    # ── pdf ──────────────────────────────────────────────────────────────
    pdf_parser = subparsers.add_parser("pdf", help="Manage PDF attachments")
    pdf_sub = pdf_parser.add_subparsers(dest="pdf_command", required=True)
    pdf_retry = pdf_sub.add_parser("retry", help="Retry PDF download for an entry")
    pdf_retry.add_argument("citekey", nargs="?")
    pdf_retry.add_argument("--config")
    add_single_target(pdf_retry)
    pdf_retry.add_argument(
        "--failed-only", action="store_true",
        help="retry PDF for all entries with no local PDF (ignores citekey argument)",
    )
    pdf_attach = pdf_sub.add_parser("attach", help="Attach a PDF by URL or file path")
    pdf_attach.add_argument("citekey")
    pdf_attach.add_argument("source")
    pdf_attach.add_argument("--config")
    add_single_target(pdf_attach)

    # ── tag ──────────────────────────────────────────────────────────────
    tag_parser = subparsers.add_parser("tag", help="Manage tags on BibTeX entries")
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
    tag_list_p.add_argument("--json", action="store_true", help="output tags as JSON")

    # ── search ───────────────────────────────────────────────────────────
    search_parser = subparsers.add_parser(
        "search", help="Search BibTeX entries by query, author, year, or tag"
    )
    search_parser.add_argument("--query")
    search_parser.add_argument("--author")
    search_parser.add_argument("--year", type=int)
    search_parser.add_argument("--tag")
    search_parser.add_argument("--config")
    add_multi_target(search_parser)
    search_parser.add_argument("--json", action="store_true", help="output matches as JSON")

    # ── update ───────────────────────────────────────────────────────────
    update_parser = subparsers.add_parser(
        "update",
        help="conservatively fill missing metadata; does not promote preprints",
        description=(
            "Conservatively enrich entries by filling missing metadata only. This does not "
            "replace preprints with published versions; use 'pzi promote' for "
            "preprint→published promotion."
        ),
    )
    update_parser.add_argument("--config")
    add_multi_target(update_parser)
    update_parser.add_argument("--dry-run", action="store_true")
    update_parser.add_argument("--verbose", action="store_true")

    # ── promote ──────────────────────────────────────────────────────────
    promote_parser = subparsers.add_parser(
        "promote",
        help="find published versions of preprints and update/create entries",
        description=(
            "Find published versions of preprint entries. By default, keeps the preprint and "
            "creates a published entry; with --replace, updates the preprint entry in place."
        ),
    )
    promote_parser.add_argument("--config")
    add_multi_target(promote_parser)
    promote_parser.add_argument("--dry-run", action="store_true")
    promote_parser.add_argument("--verbose", action="store_true")
    promote_parser.add_argument(
        "--replace", action="store_true",
        help="update the preprint entry in place instead of keeping both versions",
    )

    # ── list / set-default / doctor ──────────────────────────────────────
    list_parser = subparsers.add_parser("list", help="list configured BibTeX libraries")
    list_parser.add_argument("--config")
    list_parser.add_argument("--json", action="store_true", help="output bibs as JSON")
    set_default_parser = subparsers.add_parser("set-default", help="set default BibTeX library")
    set_default_parser.add_argument("name")
    set_default_parser.add_argument("--config")
    doctor_parser = subparsers.add_parser("doctor", help="Check configuration and service health")
    doctor_parser.add_argument("--config")

    # ── server ───────────────────────────────────────────────────────────
    server_parser = subparsers.add_parser(
        "server",
        help="Start HTTP API server (runs the translation-server as a child)",
    )
    server_parser.add_argument("--config")
    server_parser.add_argument("--host")
    server_parser.add_argument("--port", type=int)
    server_parser.add_argument("--stop-after", type=int, metavar="MINUTES",
                               help="auto-stop the whole server after N idle minutes")

    # ── init ─────────────────────────────────────────────────────────────
    init_parser = subparsers.add_parser("init", help="Create or overwrite pzi configuration")
    init_parser.add_argument("--config")
    init_parser.add_argument("--force", action="store_true", help="overwrite existing config")
    init_parser.add_argument(
        "--setup", action="store_true",
        help="write config, install translation-server, and set up browser fallback",
    )
    init_parser.add_argument(
        "--with-browser", action="store_true", help="configure and install browser fallback"
    )
    init_parser.add_argument(
        "--bib", default="~/bibs/main.bib", help="default BibTeX file path for --setup"
    )
    init_parser.add_argument(
        "--papers-dir", help="PDF storage directory for --setup; defaults to <bib-dir>/papers"
    )
    init_parser.add_argument("--name", default="main", help="default bib name for --setup")
    init_parser.add_argument("--browser", default="chromium", choices=["chromium", "firefox"],
                             help="browser for PDF fallback (default: chromium)")

    # ── services ─────────────────────────────────────────────────────────
    services_parser = subparsers.add_parser(
        "services", help="Inspect or reinstall the translation-server"
    )
    services_sub = services_parser.add_subparsers(dest="services_command", required=True)
    for command, cmd_help in [
        ("status", "Show translation-server status"),
        ("update", "Reinstall translation-server with latest pinned versions"),
    ]:
        p = services_sub.add_parser(command, help=cmd_help)
        p.add_argument("--config")

    # ── version / config / bib-stats / delete / entries / detail ────────
    _ = subparsers.add_parser("version", help="show pzi version")
    _config_parser = subparsers.add_parser("config", help="Validate or inspect pzi configuration")
    _config_sub = _config_parser.add_subparsers(dest="config_command", required=True)
    _ = _config_sub.add_parser("validate", help="validate config file")
    _.add_argument("--config")
    bib_stats_parser = subparsers.add_parser("bib-stats", help="show stats for BibTeX library")
    bib_stats_parser.add_argument("--config")
    add_single_target(bib_stats_parser)
    bib_stats_parser.add_argument("--json", action="store_true", help="output stats as JSON")
    delete_parser = subparsers.add_parser("delete", help="delete a BibTeX entry by citekey")
    delete_parser.add_argument("citekey", help="citekey of the entry to delete")
    delete_parser.add_argument("--config")
    add_single_target(delete_parser)
    delete_parser.add_argument("--dry-run", action="store_true", help="preview without deleting")
    delete_parser.add_argument("--force", action="store_true", help="skip confirmation prompt")
    entries_parser = subparsers.add_parser("entries", help="list all entries in a BibTeX library")
    entries_parser.add_argument("--config")
    entries_parser.add_argument(
        "--offset", type=int, default=0, help="pagination offset (default: 0)"
    )
    entries_parser.add_argument(
        "--limit", type=int, default=50, help="entries per page (default: 50)"
    )
    entries_parser.add_argument(
        "--sort", default="citekey", choices=["citekey", "title", "year", "author"],
        help="sort field (default: citekey)",
    )
    add_single_target(entries_parser)
    entries_parser.add_argument("--json", action="store_true", help="output entries as JSON")
    detail_parser = subparsers.add_parser(
        "detail", help="show full record detail for a single entry"
    )
    detail_parser.add_argument("citekey", help="citekey of the entry")
    detail_parser.add_argument("--config")
    detail_parser.add_argument("--json", action="store_true", help="output full record as JSON")
    add_single_target(detail_parser)

    # ── clean / dedupe / merge / export / import / reindex ──────
    clean_parser = subparsers.add_parser(
        "clean", help="check and clean a BibTeX library for integrity issues"
    )
    clean_parser.add_argument("--config")
    add_single_target(clean_parser)
    clean_parser.add_argument("--dry-run", action="store_true", help="report issues without fixing")
    clean_parser.add_argument(
        "--fix", action="store_true", help="apply fixes (move orphan PDFs, sort entries)"
    )
    clean_parser.add_argument("--json", action="store_true", help="output report as JSON")
    dedupe_parser = subparsers.add_parser(
        "dedupe", help="find duplicate entries in a BibTeX library"
    )
    dedupe_parser.add_argument("--config")
    add_single_target(dedupe_parser)
    dedupe_parser.add_argument("--json", action="store_true", help="output duplicates as JSON")
    merge_parser = subparsers.add_parser("merge", help="merge two BibTeX entries by citekey")
    merge_parser.add_argument("citekey_a", help="source citekey (will be merged into citekey_b)")
    merge_parser.add_argument("citekey_b", help="target citekey (will receive merged fields)")
    merge_parser.add_argument("--config")
    add_single_target(merge_parser)
    merge_parser.add_argument("--dry-run", action="store_true", help="preview without merging")
    export_parser = subparsers.add_parser("export", help="export BibTeX library to various formats")
    export_parser.add_argument("--config")
    add_single_target(export_parser)
    export_parser.add_argument(
        "--format", default="bibtex", choices=["bibtex", "csv", "json", "ris"],
        help="output format (default: bibtex)",
    )
    export_parser.add_argument("-o", "--output", help="output file path (default: stdout)")
    import_parser = subparsers.add_parser(
        "import", help="import entries from a BibTeX file into your library"
    )
    import_parser.add_argument("source", help="path to source .bib file")
    import_parser.add_argument("--config")
    add_single_target(import_parser)
    import_parser.add_argument("--dry-run", action="store_true", help="preview without importing")
    import_parser.add_argument("--force-new", action="store_true",
                               help="import as new entries even if duplicates are found")
    reindex_parser = subparsers.add_parser(
        "reindex", help="regenerate citekeys and fix file references"
    )
    reindex_parser.add_argument("--config")
    add_single_target(reindex_parser)
    reindex_parser.add_argument(
        "--dry-run", action="store_true", help="preview changes without applying"
    )
    reindex_parser.add_argument("--force", action="store_true", help="skip confirmation prompt")
    return parser


# ---------------------------------------------------------------------------
# Input builders
# ---------------------------------------------------------------------------


def load_text_arg(path: str, *, stdin_text: str | None = None) -> str:
    """Load text from a path or stdin marker. Pure boundary helper for CLI capture."""
    if path == "-":
        return sys.stdin.read() if stdin_text is None else stdin_text
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_add_metadata_json(
    path: str, *, stdin_text: str | None = None
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
        record["authors"] = [part.strip() for part in args.authors.split(";") if part.strip()]
    if args.tags:
        record["tags"] = parse_tag_csv(args.tags)
    return record


def build_capture_input_from_add_args(
    args: argparse.Namespace, *, bib_selector: str | None,
) -> CaptureInput:
    """Build pure capture input from parsed `pzi add` args."""
    pdf_candidates = tuple(
        PdfCandidate(value=value, source="cli", kind=_pdf_candidate_kind(value))
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


def _pdf_candidate_kind(value: str) -> str:
    if Path(value).expanduser().is_file():
        return "path"
    return "url"


def build_capture_options_from_add_args(
    args: argparse.Namespace, *, config: Mapping[str, object] | None,
) -> CaptureOptions:
    """Build pure capture run options from parsed `pzi add` args and config."""
    cfg = config or {}
    page_metadata_cmd = getattr(args, "page_metadata_cmd", None) or cfg.get("page_metadata_cmd")
    timeout = cfg.get("page_metadata_timeout_seconds", 5)
    return CaptureOptions(
        dry_run=args.dry_run,
        force_new=getattr(args, "force_new", False),
        page_metadata_cmd=(
            page_metadata_cmd
            if isinstance(page_metadata_cmd, str) and page_metadata_cmd.strip()
            else None
        ),
        page_metadata_timeout_seconds=int(timeout) if isinstance(timeout, int) else 5,
    )
