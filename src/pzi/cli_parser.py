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
from typing import NoReturn, TextIO

from pzi import cli_version_text, exit_codes
from pzi.capture_models import (
    AuthHints,
    CaptureInput,
    CaptureOptions,
    PdfCandidate,
    load_page_artifact,
)
from pzi.errors import PziError
from pzi.fileio import read_text_utf8
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


def _usage_error_lines(parser: argparse.ArgumentParser, message: str) -> list[str]:
    """Render a bad-invocation error in the canonical two-part format.

    ``<prog>: error: <message>`` followed by a pointer to the command's
    ``--help``.  Deliberately omits argparse's multi-line ``usage:`` block —
    the pointer is enough and keeps the message compact.  This is the single
    source of truth for the format so that both argparse-native errors
    (:meth:`_PziParser.error`) and the conditional checks in the command
    runners (via :func:`usage_error_lines`) look byte-for-byte identical.
    """
    return [
        f"{parser.prog}: error: {message}",
        f"Run '{parser.prog} --help' for usage.",
    ]


class _PziParser(argparse.ArgumentParser):
    """ArgumentParser that renders errors in the canonical pzi format.

    Errors are written to :attr:`error_stream` (defaulting to ``sys.stderr``)
    so the CLI can route argparse's own diagnostics through the same injected
    stream every command runner uses.  Set it with :func:`set_error_stream`.
    """

    error_stream: TextIO | None = None

    def error(self, message: str) -> NoReturn:
        stream = self.error_stream if self.error_stream is not None else sys.stderr
        for line in _usage_error_lines(self, message):
            print(line, file=stream)
        sys.exit(2)


def set_error_stream(parser: argparse.ArgumentParser, stream: TextIO) -> None:
    """Route a parser's (and all its subparsers') error output to *stream*."""
    if isinstance(parser, _PziParser):
        parser.error_stream = stream
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                set_error_stream(subparser, stream)


def usage_error_lines(command_path: tuple[str, ...], message: str) -> list[str]:
    """Canonical bad-invocation error lines for a command, by subcommand path.

    Used by command runners for *conditional* invocation errors that argparse
    cannot express (e.g. ``pzi add`` with neither a value nor ``--from-file``).
    ``command_path`` walks the subparser tree, e.g. ``("add",)`` or
    ``("pdf", "retry")``.
    """
    parser: argparse.ArgumentParser = build_parser()
    for name in command_path:
        parser = _find_subparser(parser, name)
    return _usage_error_lines(parser, message)


def _find_subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and name in action.choices:
            return action.choices[name]
    raise KeyError(f"no subparser {name!r} under {parser.prog!r}")


def _non_negative_int(value: str) -> int:
    """argparse type: a base-10 integer ``>= 0`` (rejects negatives/garbage)."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be zero or greater, got {parsed}")
    return parsed


def _tcp_port(value: str) -> int:
    """argparse type: a bindable TCP port, ``1..65535``.

    `0` would mean "let the OS choose", but nothing reports the chosen port back
    and the browser extension derives its URL from the *configured* port, so an
    ephemeral one is unusable here. The config loader already enforces this same
    range; the flag did not, so `--port 99999` reached `socket.bind` and died
    with an `OverflowError` traceback.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError(f"must be between 1 and 65535, got {parsed}")
    return parsed


def _positive_int(value: str) -> int:
    """argparse type: a base-10 integer ``>= 1``.

    Used for `--limit`, where `_non_negative_int` would let 0 through: 0 was
    silently clamped to 1 and the result envelope then reported `"limit": 1`,
    a different limit from the one asked for, with no warning. `--offset 0` is
    meaningful, so that keeps `_non_negative_int`.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be one or greater, got {parsed}")
    return parsed


def _non_negative_float(value: str) -> float:
    """argparse type: a number ``>= 0`` (rejects negatives/garbage)."""
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be zero or greater, got {parsed}")
    return parsed


# ---------------------------------------------------------------------------
# Help text: grouped command listing (plain text)
# ---------------------------------------------------------------------------


# (command, one-line description) grouped by task, most common first.
_COMMAND_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Capture", (
        ("add", "Capture a paper by DOI, URL, or PDF"),
        ("inbox", "Drain a configured inbox file into your library"),
        ("pdf", "Retry or attach a PDF for an entry"),
    )),
    ("Browse & search", (
        ("entries", "List entries, show one by citekey, or --stats"),
        ("search", "Search by query, author, year, or tag"),
        ("check", "Validate references against authoritative sources"),
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
    lines.append("EXIT CODES")
    lines.extend(
        f"  {code}  {meaning}"
        for code, meaning in (
            (0, "success"),
            (1, "ran fine, has something to report (no search matches,"),
            (" ", "   duplicates found, integrity issues, unverified citations)"),
            (2, "usage error"),
            (3, "entry not found"),
            (4, "batch partly failed: some items succeeded and some did not"),
            (" ", "   (add --from-file, import, inbox, update, update --promote;"),
            (" ", "   a batch in which *nothing* succeeded is 5)"),
            (5, "could not run (bad config, unknown --target, locked bib,"),
            (" ", "   permission denied, service unreachable)"),
            (130, "interrupted (SIGINT)"),
            (141, "downstream pipe closed (SIGPIPE)"),
        )
    )
    lines.append("")
    lines.append("ENVIRONMENT")
    lines.append("  PZI_CONFIG  config file path (--config wins over it)")
    lines.append("")
    lines.append("Run 'pzi <command> --help' for details on a command.")
    return "\n".join(lines)


def _subcommand_epilog(examples: tuple[str, ...]) -> str:
    return "\n".join(_examples_block(examples))


# ---------------------------------------------------------------------------
# Parser builder
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = _PziParser(
        prog="pzi",
        usage="pzi <command> [options]",
        description="Capture papers into local BibTeX libraries from DOI, URL, or PDF.",
        epilog=_top_level_epilog(),
        formatter_class=_PziHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=cli_version_text())
    # Also accepted before the subcommand. `pzi --config X entries` is a natural
    # invocation and used to fail with `argument command: invalid choice:
    # '/path.toml'` — argparse reading the path as the command name. The
    # subcommand-level `--config` still wins when both are given, since it is
    # the more specific one.
    parser.add_argument(
        "--config", metavar="PATH", dest="top_level_config",
        help="path to the pzi config file (may also be given after the command)",
    )
    # prog="pzi" so subcommands show `usage: pzi <command> ...`, not the parent usage.
    subparsers = parser.add_subparsers(
        dest="command", metavar="command", prog="pzi", parser_class=_PziParser
    )

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", metavar="PATH", help="path to the pzi config file")

    def add_single_target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target", help="configured library name/path or direct .bib path")

    def add_multi_target(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--target",
            # `append`, not `nargs="+"`. Repeating the flag — `--target a
            # --target b` — is now the *only* way to name several libraries, and
            # that is the point: with `nargs="+"`, `--target a b` meant "two
            # libraries" on these two commands and "one library, and `b` is the
            # positional" on the other twelve, silently and with no error on
            # either path. `pzi entries --target main main` reported "no entry
            # with citekey main".
            #
            # The greedy form cannot be kept and made safe: it also swallows a
            # command's own positional, so `pzi add --target lib 10.1234/x`
            # would lose the DOI. Removing it leaves one spelling that means the
            # same thing everywhere; `--target a b` is now a plain
            # "unrecognized arguments: b" on a command with no positional.
            #
            # `store` alone is not enough either: it kept only the last of a
            # repeated flag, so `update` asked to write two libraries wrote one.
            action="append",
            help="configured library name/path or direct .bib path (repeatable)",
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
        "--dry-run", action="store_true",
        help=(
            "preview the result without writing to the library "
            "(still queries providers, and warms the metadata cache if enabled)"
        ),
    )
    add_parser.add_argument("--verbose", action="store_true", help="show metadata diagnostics")
    add_parser.add_argument("--json", action="store_true", help="write result as JSON")
    add_parser.add_argument(
        "--strict-metadata", action="store_true",
        help=(
            "refuse to capture a paper the metadata does not identify "
            "(needs a title plus a DOI, author or year), and fail rather than "
            "fall back when no provider answered at all"
        ),
    )
    # The capture path has always read `force_new`, and the HTTP API and the
    # browser extension both expose it — but it was registered only on
    # `import`, so `getattr(args, "force_new", False)` on the add path could
    # never be true. Registering it closes the CLI/extension parity gap rather
    # than deleting a working capability.
    add_parser.add_argument(
        "--force-new", action="store_true",
        help="add as a new entry even if it looks like a duplicate",
    )

    add_batch = add_parser.add_argument_group("bulk capture")
    add_batch.add_argument(
        "--from-file", metavar="PATH",
        help="capture each DOI/URL listed in a file (one per line, '#' comments; '-' for stdin)",
    )
    add_batch.add_argument(
        # No default here: the runner needs to tell "the user asked for a delay"
        # from "nobody said", so that `--delay` outside --from-file mode can be
        # refused rather than silently ignored. The 1.0s default is applied in
        # the batch path.
        "--delay", type=_non_negative_float, metavar="SECONDS",
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

    # ── inbox ────────────────────────────────────────────────────────────
    inbox_parser = subparsers.add_parser(
        "inbox",
        help="Drain an inbox file of URLs/DOIs into your library",
        description=(
            "Process a plain-text inbox file: add every URL/DOI, then remove the "
            "entries that succeed and keep the failures for a later retry.\n\n"
            "External tools (cron, email filters, shell aliases) append lines; "
            "'pzi inbox <file>' consumes them.\n\n"
            "Inbox line format: <doi|url> [#tag1 #tag2] [@bib-name]"
        ),
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi inbox ~/papers/inbox.txt",
            "pzi inbox ~/papers/inbox.txt --dry-run",
            "pzi inbox ~/papers/inbox.txt --tags ml --delay 2",
        )),
    )
    inbox_parser.add_argument(
        "file", metavar="FILE", help="path to the inbox file to drain",
    )
    inbox_parser.add_argument(
        "--dry-run", action="store_true",
        help="preview what would be added without writing to the library or inbox file",
    )
    inbox_parser.add_argument(
        "--tags", help="comma-separated tags applied to all entries in this drain run",
    )
    inbox_parser.add_argument(
        "--delay", type=_non_negative_float, default=1.0, metavar="SECONDS",
        help="pause between items, with jitter (default: 1.0)",
    )
    inbox_parser.add_argument(
        "--json", action="store_true", help="output the result as JSON"
    )
    add_config(inbox_parser)

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
    pdf_sub = pdf_parser.add_subparsers(
        # Not `required=True`: argparse's message for a missing one names
        # the internal dest (`pdf_command`), which appears in no
        # documentation. `pzi.cli` prints this group's help instead.
        dest="pdf_command", required=False, parser_class=_PziParser,
    )
    pdf_retry = pdf_sub.add_parser("retry", help="Retry PDF download for an entry")
    pdf_retry.add_argument("citekey", nargs="?", help="citekey of the entry to retry")
    add_config(pdf_retry)
    add_single_target(pdf_retry)
    pdf_retry.add_argument(
        "--failed-only", action="store_true",
        help="retry PDF for all entries with no local PDF (cannot be combined with a citekey)",
    )
    pdf_retry.add_argument(
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )
    pdf_attach = pdf_sub.add_parser("attach", help="Attach a PDF by URL or file path")
    pdf_attach.add_argument("citekey", help="citekey of the entry to attach to")
    pdf_attach.add_argument("source", help="PDF URL or local file path")
    add_config(pdf_attach)
    add_single_target(pdf_attach)
    pdf_attach.add_argument(
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )

    # ── tag ──────────────────────────────────────────────────────────────
    tag_parser = subparsers.add_parser("tag", help="Manage tags on BibTeX entries")
    tag_sub = tag_parser.add_subparsers(
        # Not `required=True`: argparse's message for a missing one names
        # the internal dest (`tag_command`), which appears in no
        # documentation. `pzi.cli` prints this group's help instead.
        dest="tag_command", required=False, parser_class=_PziParser,
    )
    tag_add_p = tag_sub.add_parser("add", help="Add tags to an entry")
    tag_add_p.add_argument("citekey", help="citekey of the entry")
    tag_add_p.add_argument("tags", nargs="+", help="one or more tags to add")
    add_config(tag_add_p)
    add_single_target(tag_add_p)
    tag_add_p.add_argument("--dry-run", action="store_true", help="preview without writing")
    tag_add_p.add_argument("--json", action="store_true", help="output the result as JSON")
    tag_rm_p = tag_sub.add_parser("remove", help="Remove tags from an entry")
    tag_rm_p.add_argument("citekey", help="citekey of the entry")
    tag_rm_p.add_argument("tags", nargs="+", help="one or more tags to remove")
    add_config(tag_rm_p)
    add_single_target(tag_rm_p)
    tag_rm_p.add_argument("--dry-run", action="store_true", help="preview without writing")
    tag_rm_p.add_argument("--json", action="store_true", help="output the result as JSON")
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
    search_parser.add_argument("--query", help="match title, abstract, and note text")
    search_parser.add_argument("--author", help="match author name")
    search_parser.add_argument("--year", type=int, help="match publication year")
    search_parser.add_argument("--tag", help="match an attached tag")
    add_config(search_parser)
    add_multi_target(search_parser)
    search_parser.add_argument("--json", action="store_true", help="output matches as JSON")

    # ── check ────────────────────────────────────────────────────────────
    check_parser = subparsers.add_parser(
        "check",
        help="Validate references against authoritative metadata sources",
        description=(
            "Read-only audit: verify each entry exists with the claimed metadata "
            "across Crossref/OpenAlex/DBLP/OpenReview/Semantic Scholar, flagging "
            "fabricated or mismatched references. Never writes the library."
        ),
        formatter_class=_PziHelpFormatter,
        epilog=_subcommand_epilog((
            "pzi check",
            "pzi check --report audit.json",
            "pzi check --strict --jsonl audit.jsonl",
        )),
    )
    check_parser.add_argument(
        "--strict",
        action="store_true",
        help="tighten the verdict gate and add the single-edit title / truncated author checks",
    )
    check_parser.add_argument(
        "--report", metavar="PATH", help="write the full result as JSON to PATH"
    )
    check_parser.add_argument(
        "--jsonl", metavar="PATH",
        help="write one JSON object per entry to PATH ('-' for stdout)",
    )
    check_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing --report / --jsonl file",
    )
    add_config(check_parser)
    add_single_target(check_parser)
    check_parser.add_argument("--json", action="store_true", help="output the result as JSON")

    # ── update ───────────────────────────────────────────────────────────
    update_parser = subparsers.add_parser(
        "update",
        help="Fill missing metadata; with --promote, replace preprints with published versions",
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
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )
    update_parser.add_argument(
        "--promote", action="store_true",
        help="find published versions of preprints and update/create entries",
    )
    update_parser.add_argument(
        "--replace", action="store_true",
        help="with --promote, update the preprint entry in place instead of keeping both",
    )
    update_parser.add_argument(
        "--mark-resolved", action="store_true",
        help="with --promote, tag promoted preprints and skip already-tagged ones on re-runs",
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
    doctor_parser.add_argument(
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )

    # ── server ───────────────────────────────────────────────────────────
    server_parser = subparsers.add_parser(
        "server",
        help="Start HTTP API server (runs the translation-server as a child)",
    )
    add_config(server_parser)
    server_parser.add_argument("--host", help="bind host (default: api_listen_host, 127.0.0.1)")
    server_parser.add_argument(
        "--port", type=_tcp_port, help="bind port (default: api_listen_port, 8765)"
    )
    # `>= 1`: omitting the flag already means "never stop", so 0 would be a
    # second spelling of the default — and it used to mean "stop at the first
    # 30s tick regardless of traffic".
    server_parser.add_argument("--stop-after", type=_positive_int, metavar="MINUTES",
                               help="auto-stop the whole server after N idle minutes")
    # A flag, deliberately not a config key: nothing reachable from config.toml
    # or from the HTTP API itself may switch authentication off.
    server_parser.add_argument(
        "--no-auth", action="store_true",
        # It does not disable authentication. It only lifts the refusal to
        # start when *no* token exists — with one configured it changes
        # nothing, while the old wording promised the opposite, so a user
        # reading it would think they had opened the API up (or, worse, that
        # they could not close it).
        help=(
            "start without an API token when none is configured "
            "(does not disable auth if a token exists)"
        ),
    )
    server_parser.add_argument(
        "--log-requests",
        action="store_true",
        help="log one line per HTTP request to stderr (method, path, status, ms)",
    )

    # ── init ─────────────────────────────────────────────────────────────
    init_parser = subparsers.add_parser("init", help="Create or overwrite pzi configuration")
    add_config(init_parser)
    init_parser.add_argument("--force", action="store_true", help="overwrite existing config")
    init_parser.add_argument(
        "--setup", action="store_true",
        help="write config, configure translation-server, and configure browser fallback",
    )
    # These four default to None, not to their effective values, so the runner
    # can tell "user asked for this" from "nobody said" and refuse them without
    # --setup instead of accepting and dropping them. Their defaults are applied
    # in the runner's --setup branch.
    init_parser.add_argument(
        "--bib", help="default BibTeX file path for --setup (default: ~/bibs/main.bib)"
    )
    init_parser.add_argument(
        "--papers-dir", help="PDF storage directory for --setup; defaults to <bib-dir>/papers"
    )
    init_parser.add_argument(
        "--name", help="default bib name for --setup (default: main)"
    )
    init_parser.add_argument("--browser", choices=["chromium", "firefox"],
                             help="browser for PDF fallback (default: chromium)")
    init_parser.add_argument(
        "--rotate-token", action="store_true",
        help="replace the existing API auth token (this un-pairs the browser extension)",
    )

    # ── delete / entries ─────────────────────────────────────────────────
    delete_parser = subparsers.add_parser("delete", help="Delete a BibTeX entry by citekey")
    delete_parser.add_argument("citekey", help="citekey of the entry to delete")
    add_config(delete_parser)
    add_single_target(delete_parser)
    delete_parser.add_argument("--dry-run", action="store_true", help="preview without deleting")
    delete_parser.add_argument("--force", action="store_true", help="skip confirmation prompt")
    delete_parser.add_argument(
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )
    entries_parser = subparsers.add_parser(
        "entries",
        help="List entries, show one by citekey, or show library stats",
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
    # These three default to None, not to their effective values, so the runner
    # can tell "the user asked for this" from "nobody said" — and refuse them on
    # the detail and stats subpaths, which parse them and cannot apply them. The
    # `init` parser defaults its four flags to None for the same reason.
    entries_parser.add_argument(
        "--offset", type=_non_negative_int, default=None,
        help="pagination offset (default: 0)",
    )
    entries_parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="entries per page, 1-500 (default: 50; larger values are capped at 500)",
    )
    entries_parser.add_argument(
        "--sort", default=None, choices=["citekey", "title", "year", "author"],
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
    fix_sub = fix_parser.add_subparsers(
        # Not `required=True`: argparse's message for a missing one names
        # the internal dest (`fix_command`), which appears in no
        # documentation. `pzi.cli` prints this group's help instead.
        dest="fix_command", required=False, parser_class=_PziParser,
    )

    clean_parser = fix_sub.add_parser(
        "clean", help="Check and clean a BibTeX library for integrity issues"
    )
    add_config(clean_parser)
    add_single_target(clean_parser)
    clean_parser.add_argument("--dry-run", action="store_true", help="report issues without fixing")
    clean_parser.add_argument(
        "--fix", action="store_true", help="apply fixes (move orphan PDFs)"
    )
    clean_parser.add_argument("--json", action="store_true", help="output report as JSON")

    dedupe_parser = fix_sub.add_parser(
        "dedupe", help="Find duplicate entries in a BibTeX library"
    )
    add_config(dedupe_parser)
    add_single_target(dedupe_parser)
    dedupe_parser.add_argument("--json", action="store_true", help="output duplicates as JSON")

    merge_parser = fix_sub.add_parser("merge", help="Merge two BibTeX entries by citekey")
    merge_parser.add_argument("citekey_a", help="source citekey (will be merged into citekey_b)")
    merge_parser.add_argument("citekey_b", help="target citekey (will receive merged fields)")
    add_config(merge_parser)
    add_single_target(merge_parser)
    merge_parser.add_argument("--dry-run", action="store_true", help="preview without merging")
    merge_parser.add_argument(
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )

    reindex_parser = fix_sub.add_parser(
        "reindex",
        help="Audit citekeys against citekey_format (rename only with --rename-citekeys)",
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
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )
    reindex_parser.add_argument(
        "--dry-run", action="store_true",
        help="with --rename-citekeys, preview the renames without applying",
    )
    reindex_parser.add_argument(
        "--force", action="store_true", help="skip confirmation prompt",
    )

    # ── export / import ──────────────────────────────────────────────────
    export_parser = subparsers.add_parser(
        "export",
        help="Export BibTeX library to various formats",
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
        "import", help="Import entries from a BibTeX file into your library"
    )
    import_parser.add_argument(
        "source", help="path to source .bib file, or - to read BibTeX from stdin"
    )
    add_config(import_parser)
    add_single_target(import_parser)
    import_parser.add_argument("--dry-run", action="store_true", help="preview without importing")
    import_parser.add_argument("--force-new", action="store_true",
                               help="import as new entries even if duplicates are found")
    import_parser.add_argument(
        "--json", action="store_true", help="emit the result as a JSON envelope",
    )
    # The three group parsers, so a bare `pzi fix` / `pzi tag` / `pzi pdf` can be
    # answered with that group's own help rather than an argparse message naming
    # an internal dest. Reaching them through `parser._subparsers` would mean
    # depending on argparse internals; handing them over explicitly does not.
    parser.pzi_group_parsers = {  # type: ignore[attr-defined]
        "fix": fix_parser,
        "tag": tag_parser,
        "pdf": pdf_parser,
    }
    return parser


# ---------------------------------------------------------------------------
# Input builders
# ---------------------------------------------------------------------------


def load_text_arg(path: str, *, stdin_text: str | None = None) -> str:
    """Load text from a path or stdin marker. Pure boundary helper for CLI capture."""
    if path == "-":
        return sys.stdin.read() if stdin_text is None else stdin_text
    return read_text_utf8(path)


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
    """Load record metadata JSON for `pzi add --metadata-json`.

    Raises :class:`PziError` with a ``USAGE`` code, not a bare ``ValueError``:
    the file is user input, and the CLI boundary deliberately lets unrecognized
    exceptions propagate as tracebacks so genuine bugs stay visible. A typo in a
    hand-written JSON file is not a bug in pzi.
    """
    raw = load_text_arg(path, stdin_text=stdin_text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PziError(
            f"--metadata-json is not valid JSON: {exc}",
            code=exit_codes.USAGE,
        ) from exc
    if not isinstance(payload, dict):
        raise PziError(
            "--metadata-json must contain a JSON object",
            code=exit_codes.USAGE,
        )
    return dict(payload)


def describe_invalid_metadata_json(
    path: str | None, *, stdin_text: str | None = None
) -> str | None:
    """Return a usage message when ``--metadata-json`` cannot be read, else None.

    Mirrors :func:`describe_invalid_add_input` so the add runner can reject the
    file in its fail-fast block — *before* the translation-server backend is
    started. Parsing it only at capture time meant a one-character JSON typo cost
    a full Node/translation-server startup and then produced a traceback.
    """
    if not path:
        return None
    try:
        load_add_metadata_json(path, stdin_text=stdin_text)
    except PziError as exc:
        return exc.message
    except OSError as exc:
        return f"cannot read --metadata-json {path}: {exc.strerror or exc}"
    return None


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
        metadata_strict=getattr(args, "strict_metadata", False),
        page_metadata_cmd=(
            page_metadata_cmd
            if isinstance(page_metadata_cmd, str) and page_metadata_cmd.strip()
            else None
        ),
        page_metadata_timeout_seconds=int(timeout) if isinstance(timeout, int) else 5,
    )
