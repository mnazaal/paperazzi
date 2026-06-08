"""CLI parser and argument normalization helpers."""

from __future__ import annotations

import argparse

from pzi.tag_service import parse_tag_csv
from pzi import cli_version_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pzi")
    parser.add_argument("--version", action="version", version=cli_version_text())
    subparsers = parser.add_subparsers(dest="command")

    def add_single_target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--target", help="configured library name/path or direct .bib path")

    def add_multi_target(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--target",
            nargs="+",
            help="one or more configured library names/paths or direct .bib paths",
        )

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("value")
    add_parser.add_argument("--citekey")
    add_parser.add_argument("--title")
    add_parser.add_argument("--year", type=int)
    add_parser.add_argument("--authors")
    add_parser.add_argument("--tags")
    add_parser.add_argument("--config")
    add_single_target(add_parser)
    add_parser.add_argument("--dry-run", action="store_true")
    add_parser.add_argument("--verbose", action="store_true")

    pdf_parser = subparsers.add_parser("pdf")
    pdf_sub = pdf_parser.add_subparsers(dest="pdf_command", required=True)
    pdf_retry = pdf_sub.add_parser("retry")
    pdf_retry.add_argument("citekey")
    pdf_retry.add_argument("--config")
    add_single_target(pdf_retry)
    pdf_attach = pdf_sub.add_parser("attach")
    pdf_attach.add_argument("citekey")
    pdf_attach.add_argument("source")
    pdf_attach.add_argument("--config")
    add_single_target(pdf_attach)

    tag_parser = subparsers.add_parser("tag")
    tag_sub = tag_parser.add_subparsers(dest="tag_command", required=True)
    tag_add_p = tag_sub.add_parser("add")
    tag_add_p.add_argument("citekey")
    tag_add_p.add_argument("tags", nargs="+")
    tag_add_p.add_argument("--config")
    add_single_target(tag_add_p)
    tag_add_p.add_argument("--dry-run", action="store_true")
    tag_rm_p = tag_sub.add_parser("remove")
    tag_rm_p.add_argument("citekey")
    tag_rm_p.add_argument("tags", nargs="+")
    tag_rm_p.add_argument("--config")
    add_single_target(tag_rm_p)
    tag_rm_p.add_argument("--dry-run", action="store_true")
    tag_list_p = tag_sub.add_parser("list")
    tag_list_p.add_argument("citekey", nargs="?")
    tag_list_p.add_argument("--config")
    add_single_target(tag_list_p)

    search_parser = subparsers.add_parser("search")
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

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config")

    server_parser = subparsers.add_parser("server")
    server_parser.add_argument("--config")
    server_parser.add_argument("--host")
    server_parser.add_argument("--port", type=int)
    server_parser.add_argument(
        "--stop-after",
        type=int,
        metavar="MINUTES",
        help="auto-stop translation-server after N idle minutes",
    )

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--config")
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite existing config"
    )
    init_parser.add_argument(
        "--setup",
        action="store_true",
        help="write config, managed services, and browser fallback",
    )
    init_parser.add_argument(
        "--with-services", action="store_true", help="write managed service files"
    )
    init_parser.add_argument(
        "--with-browser",
        action="store_true",
        help="configure and install browser fallback",
    )
    init_parser.add_argument(
        "--with-flaresolverr",
        action="store_true",
        help="include FlareSolverr service and config",
    )
    init_parser.add_argument(
        "--bib", default="~/bibs/main.bib", help="default BibTeX file path for --setup"
    )
    init_parser.add_argument(
        "--papers-dir",
        help="PDF storage directory for --setup; defaults to <bib-dir>/papers",
    )
    init_parser.add_argument("--name", default="main", help="default bib name for --setup")

    services_parser = subparsers.add_parser("services")
    services_sub = services_parser.add_subparsers(
        dest="services_command", required=True
    )
    for command in ("up", "down", "status"):
        p = services_sub.add_parser(command)
        p.add_argument("--config")

    browser_parser = subparsers.add_parser("browser")
    browser_sub = browser_parser.add_subparsers(dest="browser_command", required=True)
    browser_install = browser_sub.add_parser("install")
    browser_install.add_argument("browser", nargs="?", default="chromium")
    browser_install.add_argument("--config")

    _ = subparsers.add_parser("version", help="show pzi version")

    _config_parser = subparsers.add_parser("config")
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

    return parser


def build_record_overrides_from_add_args(args: argparse.Namespace) -> dict[str, object]:
    record: dict[str, object] = {}

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
