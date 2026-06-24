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
# Help text: grouped command listing (plain text)
# ---------------------------------------------------------------------------


# (command, one-line description) grouped by task, most common first.
_COMMAND_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Capture", (
        ("add", "Capture a paper by DOI, URL, or PDF"),
        ("pdf", "Retry or attach a PDF for an entry"),
    )),
    ("Browse & search", (
        ("entries", "List entries, show one by citekey, or --stats"),
        ("search", "Search by query, author, year, or tag"),
        ("tag", "Add, remove, or list tags"),
    )),
    ("Maintain", (
        ("update", "Fill missing metadata; --promote replaces preprints"),
        ("fix", "Clean, dedupe, merge, or reindex a library"),
        ("delete", "Delete an entry by citekey"),
        ("import", "Import entries from a .bib file"),
        ("export", "Export to BibTeX, CSV, JSON, or RIS"),
    )),
    ("Setup & server", (
        ("init", "Create or overwrite the configuration"),
        ("server", "Run the HTTP API for the browser extension"),
        ("doctor", "Check config/health; reinstall the translation-server"),
    )),
)

_TOP_LEVEL_EXAMPLES: tuple[str, ...] = (
    "pzi add https://arxiv.org/abs/2301.07041",
    "pzi add 10.1145/1327452.1327492 --tags systems,classic",
    "pzi search --author hinton --year 2015",
    "pzi export --format ris -o refs.ris",
)


def _examples_block(examples: tuple[str, ...]) -> list[str]:
    return ["EXAMPLES", *(f"  {ex}" for ex in examples)]


def _top_level_epilog() -> str:
    width = max(len(name) for _, cmds in _COMMAND_GROUPS for name, _ in cmds)
    lines = [*_examples_block(_TOP_LEVEL_EXAMPLES), ""]
    for title, cmds in _COMMAND_GROUPS:
        lines.append(title.upper())
        lines.extend(f"  {name.ljust(width)}  {desc}" for name, desc in cmds)
        lines.append("")
    lines.append("Run 'pzi <command> --help' for details on a command.")
    return "\n".join(lines)


def _subcommand_epilog(examples: tuple[str, ...]) -> str:
    return "\n".join(_examples_block(examples))


# ---------------------------------------------------------------------------
# Parser builder
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pzi",
        usage="pzi <command> [options]",
        description="Capture papers into local BibTeX libraries from DOI, URL, or PDF.",
        epilog=_top_level_epilog(),
        formatter_class=_PziHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=cli_version_text())
    # prog="pzi" so subcommands show `usage: pzi <command> ...`, not the parent usage.
    subparsers = parser.add_subparsers(dest="command", metavar="command", prog="pzi")

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", metavar="PATH", help="path to the pzi config file")

    def add_single_target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target", help="configured library name/path or direct .bib path")

    def add_multi_target(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--target",
            nargs="+",
            help="one or more configured library names/paths or direct .bib paths",
        )

    # ── add ─────────────────────────────────────────────────────────────
    add_parser = subparsers.add_parser(
        "add",
        help="Capture a paper by DOI, URL, or PDF path",
        description="Capture a paper by DOI, URL, or local PDF path into a BibTeX library.",
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi add https://arxiv.org/abs/2301.07041",
            "pzi add 10.1145/1327452.1327492 --tags ml,systems",
            "pzi add ~/Downloads/paper.pdf --dry-run",
            "pzi add --from-file urls.txt --tags ml",
            "cat urls.txt | pzi add --from-file -",
        )),
    )
    add_parser.add_argument(
        "value", metavar="<doi|url|pdf>", nargs="?", help="DOI, URL, or local PDF path"
    )
    add_config(add_parser)
    add_single_target(add_parser)
    add_parser.add_argument(
        "--dry-run", action="store_true", help="preview the result without writing"
    )
    add_parser.add_argument("--verbose", action="store_true", help="show metadata diagnostics")
    add_parser.add_argument("--json", action="store_true", help="write result as JSON")

    add_batch = add_parser.add_argument_group("bulk capture")
    add_batch.add_argument(
        "--from-file", metavar="PATH",
        help="capture each DOI/URL listed in a file (one per line, '#' comments; '-' for stdin)",
    )
    add_batch.add_argument(
        "--delay", type=float, default=1.0, metavar="SECONDS",
        help="pause between items in --from-file mode, with jitter (default: 1.0)",
    )
    add_batch.add_argument(
        "--failures-out", metavar="PATH",
        help="write failed items here for retry (default: <input>.failed.txt)",
    )

    add_meta = add_parser.add_argument_group("metadata overrides")
    add_meta.add_argument("--citekey", help="use this citekey instead of the generated one")
    add_meta.add_argument("--tags", help="comma-separated tags to attach")
    add_meta.add_argument(
        "--metadata-json",
        help="merge record metadata (title, year, authors, …) from a JSON file, or '-' for stdin",
    )

    add_hints = add_parser.add_argument_group("capture hints")
    add_hints.add_argument(
        "--cookie-file", help="read browser Cookie header text from file, or '-' for stdin"
    )
    add_hints.add_argument(
        "--pdf-candidate", action="append", default=[],
        help="candidate PDF URL/path to try; may be repeated",
    )
    add_hints.add_argument(
        "--page-html",
        help="read captured page HTML from a file (or '-' for stdin) to extract embedded metadata",
    )

    # ── pdf ──────────────────────────────────────────────────────────────
    pdf_parser = subparsers.add_parser(
        "pdf",
        help="Manage PDF attachments",
        description="Retry PDF download or attach a PDF for an existing entry.",
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi pdf retry smith2020graph",
            "pzi pdf retry --failed-only",
            "pzi pdf attach smith2020graph ~/Downloads/paper.pdf",
        )),
    )
    pdf_sub = pdf_parser.add_subparsers(dest="pdf_command", required=True)
    pdf_retry = pdf_sub.add_parser("retry", help="Retry PDF download for an entry")
    pdf_retry.add_argument("citekey", nargs="?", help="citekey of the entry to retry")
    add_config(pdf_retry)
    add_single_target(pdf_retry)
    pdf_retry.add_argument(
        "--failed-only", action="store_true",
        help="retry PDF for all entries with no local PDF (ignores citekey argument)",
    )
    pdf_attach = pdf_sub.add_parser("attach", help="Attach a PDF by URL or file path")
    pdf_attach.add_argument("citekey", help="citekey of the entry to attach to")
    pdf_attach.add_argument("source", help="PDF URL or local file path")
    add_config(pdf_attach)
    add_single_target(pdf_attach)

    # ── tag ──────────────────────────────────────────────────────────────
    tag_parser = subparsers.add_parser("tag", help="Manage tags on BibTeX entries")
    tag_sub = tag_parser.add_subparsers(dest="tag_command", required=True)
    tag_add_p = tag_sub.add_parser("add", help="Add tags to an entry")
    tag_add_p.add_argument("citekey", help="citekey of the entry")
    tag_add_p.add_argument("tags", nargs="+", help="one or more tags to add")
    add_config(tag_add_p)
    add_single_target(tag_add_p)
    tag_add_p.add_argument("--dry-run", action="store_true", help="preview without writing")
    tag_rm_p = tag_sub.add_parser("remove", help="Remove tags from an entry")
    tag_rm_p.add_argument("citekey", help="citekey of the entry")
    tag_rm_p.add_argument("tags", nargs="+", help="one or more tags to remove")
    add_config(tag_rm_p)
    add_single_target(tag_rm_p)
    tag_rm_p.add_argument("--dry-run", action="store_true", help="preview without writing")
    tag_list_p = tag_sub.add_parser("list", help="List tags for an entry or all tags")
    tag_list_p.add_argument("citekey", nargs="?", help="entry to list tags for (omit for all tags)")
    add_config(tag_list_p)
    add_single_target(tag_list_p)
    tag_list_p.add_argument("--json", action="store_true", help="output tags as JSON")

    # ── search ───────────────────────────────────────────────────────────
    search_parser = subparsers.add_parser(
        "search",
        help="Search BibTeX entries by query, author, year, or tag",
        description="Search entries; combine filters to narrow results.",
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            'pzi search --query "graph neural"',
            "pzi search --author hinton --year 2015",
            "pzi search --tag systems --json",
        )),
    )
    search_parser.add_argument("--query", help="match title and abstract text")
    search_parser.add_argument("--author", help="match author name")
    search_parser.add_argument("--year", type=int, help="match publication year")
    search_parser.add_argument("--tag", help="match an attached tag")
    add_config(search_parser)
    add_multi_target(search_parser)
    search_parser.add_argument("--json", action="store_true", help="output matches as JSON")

    # ── update ───────────────────────────────────────────────────────────
    update_parser = subparsers.add_parser(
        "update",
        help="fill missing metadata; with --promote, replace preprints with published versions",
        description=(
            "Conservatively enrich entries by filling missing metadata. By default this only "
            "fills gaps and never replaces a preprint with its published version. Pass "
            "--promote to find published versions of preprint entries; by default that keeps "
            "the preprint and creates a published entry, and with --replace it updates the "
            "preprint entry in place."
        ),
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi update",
            "pzi update --promote --dry-run",
            "pzi update --promote --replace",
        )),
    )
    add_config(update_parser)
    add_multi_target(update_parser)
    update_parser.add_argument(
        "--dry-run", action="store_true", help="preview changes without writing"
    )
    update_parser.add_argument("--verbose", action="store_true", help="show metadata diagnostics")
    update_parser.add_argument(
        "--promote", action="store_true",
        help="find published versions of preprints and update/create entries",
    )
    update_parser.add_argument(
        "--replace", action="store_true",
        help="with --promote, update the preprint entry in place instead of keeping both",
    )

    # ── doctor ───────────────────────────────────────────────────────────
    doctor_parser = subparsers.add_parser("doctor", help="Check configuration and service health")
    add_config(doctor_parser)
    doctor_parser.add_argument(
        "--config-only", action="store_true",
        help="validate the configuration only (offline; skip live service probes)",
    )
    doctor_parser.add_argument(
        "--reinstall-server", action="store_true",
        help="reinstall the translation-server with the latest pinned versions",
    )

    # ── server ───────────────────────────────────────────────────────────
    server_parser = subparsers.add_parser(
        "server",
        help="Start HTTP API server (runs the translation-server as a child)",
    )
    add_config(server_parser)
    server_parser.add_argument("--host", help="bind host (default: api_listen_host, 127.0.0.1)")
    server_parser.add_argument(
        "--port", type=int, help="bind port (default: api_listen_port, 8765)"
    )
    server_parser.add_argument("--stop-after", type=int, metavar="MINUTES",
                               help="auto-stop the whole server after N idle minutes")

    # ── init ─────────────────────────────────────────────────────────────
    init_parser = subparsers.add_parser("init", help="Create or overwrite pzi configuration")
    add_config(init_parser)
    init_parser.add_argument("--force", action="store_true", help="overwrite existing config")
    init_parser.add_argument(
        "--setup", action="store_true",
        help="write config, configure translation-server, and configure browser fallback",
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

    # ── delete / entries ─────────────────────────────────────────────────
    delete_parser = subparsers.add_parser("delete", help="delete a BibTeX entry by citekey")
    delete_parser.add_argument("citekey", help="citekey of the entry to delete")
    add_config(delete_parser)
    add_single_target(delete_parser)
    delete_parser.add_argument("--dry-run", action="store_true", help="preview without deleting")
    delete_parser.add_argument("--force", action="store_true", help="skip confirmation prompt")
    entries_parser = subparsers.add_parser(
        "entries",
        help="list entries, show one by citekey, or show library stats",
        description=(
            "List entries in a library. Pass a CITEKEY to show the full record for one entry, "
            "or --stats to show library-wide statistics."
        ),
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi entries",
            "pzi entries --sort year --limit 20",
            "pzi entries smith2024graph",
            "pzi entries --stats",
        )),
    )
    entries_parser.add_argument(
        "citekey", nargs="?", help="show the full record for this entry (omit to list)"
    )
    add_config(entries_parser)
    entries_parser.add_argument(
        "--stats", action="store_true", help="show library statistics instead of listing entries"
    )
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
    entries_parser.add_argument(
        "--json", action="store_true", help="output entries, the record, or stats as JSON"
    )

    # ── fix (maintenance: clean / dedupe / merge / reindex) ──────────────
    fix_parser = subparsers.add_parser(
        "fix",
        help="Library maintenance: clean, dedupe, merge, reindex",
        description="Library integrity and maintenance operations.",
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi fix clean --fix",
            "pzi fix dedupe --json",
            "pzi fix merge smith2024 smith2024dup",
            "pzi fix reindex --rename-citekeys --dry-run",
        )),
    )
    fix_sub = fix_parser.add_subparsers(dest="fix_command", required=True)

    clean_parser = fix_sub.add_parser(
        "clean", help="check and clean a BibTeX library for integrity issues"
    )
    add_config(clean_parser)
    add_single_target(clean_parser)
    clean_parser.add_argument("--dry-run", action="store_true", help="report issues without fixing")
    clean_parser.add_argument(
        "--fix", action="store_true", help="apply fixes (move orphan PDFs)"
    )
    clean_parser.add_argument("--json", action="store_true", help="output report as JSON")

    dedupe_parser = fix_sub.add_parser(
        "dedupe", help="find duplicate entries in a BibTeX library"
    )
    add_config(dedupe_parser)
    add_single_target(dedupe_parser)
    dedupe_parser.add_argument("--json", action="store_true", help="output duplicates as JSON")

    merge_parser = fix_sub.add_parser("merge", help="merge two BibTeX entries by citekey")
    merge_parser.add_argument("citekey_a", help="source citekey (will be merged into citekey_b)")
    merge_parser.add_argument("citekey_b", help="target citekey (will receive merged fields)")
    add_config(merge_parser)
    add_single_target(merge_parser)
    merge_parser.add_argument("--dry-run", action="store_true", help="preview without merging")

    reindex_parser = fix_sub.add_parser(
        "reindex",
        help="audit citekeys against citekey_format (rename only with --rename-citekeys)",
        description=(
            "Report which citekeys do not match citekey_format. By default this is read-only "
            "and changes nothing, keeping citekeys stable. Pass --rename-citekeys to rewrite "
            "them — this also renames the matching PDFs and WILL break any \\cite{} that uses "
            "the old keys."
        ),
        formatter_class=_PziHelpFormatter,
    )
    add_config(reindex_parser)
    add_single_target(reindex_parser)
    reindex_parser.add_argument(
        "--rename-citekeys", action="store_true",
        help="rewrite citekeys to match citekey_format (breaks \\cite{} using the old keys)",
    )
    reindex_parser.add_argument(
        "--dry-run", action="store_true",
        help="with --rename-citekeys, preview the renames without applying",
    )

    # ── export / import ──────────────────────────────────────────────────
    export_parser = subparsers.add_parser(
        "export",
        help="export BibTeX library to various formats",
        description="Export the library to BibTeX, CSV, JSON, or RIS (stdout by default).",
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi export --format ris -o refs.ris",
            "pzi export --format json | jq .",
        )),
    )
    add_config(export_parser)
    add_single_target(export_parser)
    export_parser.add_argument(
        "--format", default="bibtex", choices=["bibtex", "csv", "json", "ris"],
        help="output format (default: bibtex)",
    )
    export_parser.add_argument("-o", "--output", help="output file path (default: stdout)")
    export_parser.add_argument(
        "--force", action="store_true", help="overwrite output file if it exists"
    )
    import_parser = subparsers.add_parser(
        "import", help="import entries from a BibTeX file into your library"
    )
    import_parser.add_argument("source", help="path to source .bib file")
    add_config(import_parser)
    add_single_target(import_parser)
    import_parser.add_argument("--dry-run", action="store_true", help="preview without importing")
    import_parser.add_argument("--force-new", action="store_true",
                               help="import as new entries even if duplicates are found")
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


def parse_batch_values(text: str) -> list[str]:
    """Parse `pzi add --from-file` input: one DOI/URL per line.

    Skips blank lines and ``#`` comments, trims whitespace, and de-duplicates
    while preserving first-seen order.
    """
    seen: set[str] = set()
    values: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line not in seen:
            seen.add(line)
            values.append(line)
    return values


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
    page_metadata_cmd = cfg.get("page_metadata_cmd")  # config-only (no per-invocation flag)
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
