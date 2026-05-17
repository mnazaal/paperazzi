"""CLI entrypoints for pzi."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import TextIO

from pzi import setup_service
from pzi.add_service import add_input_to_bib
from pzi.bib_service import list_bibs, set_default_bib
from pzi.config import default_config_path
from pzi.doctor_service import doctor_check
from pzi.pdf_service import attach_pdf, retry_pdf
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib
from pzi.tag_service import add_tags, list_tags, parse_tag_csv, remove_tags
from pzi.update_service import update_bib


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pzi")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("value")
    add_parser.add_argument("--citekey")
    add_parser.add_argument("--title")
    add_parser.add_argument("--year", type=int)
    add_parser.add_argument("--authors")
    add_parser.add_argument("--tags")
    add_parser.add_argument("--bib")
    add_parser.add_argument("--config")
    add_parser.add_argument("--dry-run", action="store_true")

    pdf_parser = subparsers.add_parser("pdf")
    pdf_sub = pdf_parser.add_subparsers(dest="pdf_command", required=True)
    pdf_retry = pdf_sub.add_parser("retry")
    pdf_retry.add_argument("citekey")
    pdf_retry.add_argument("--bib")
    pdf_retry.add_argument("--config")
    pdf_attach = pdf_sub.add_parser("attach")
    pdf_attach.add_argument("citekey")
    pdf_attach.add_argument("source")
    pdf_attach.add_argument("--bib")
    pdf_attach.add_argument("--config")

    tag_parser = subparsers.add_parser("tag")
    tag_sub = tag_parser.add_subparsers(dest="tag_command", required=True)
    tag_add_p = tag_sub.add_parser("add")
    tag_add_p.add_argument("citekey")
    tag_add_p.add_argument("tags", nargs="+")
    tag_add_p.add_argument("--bib")
    tag_add_p.add_argument("--config")
    tag_add_p.add_argument("--dry-run", action="store_true")
    tag_rm_p = tag_sub.add_parser("remove")
    tag_rm_p.add_argument("citekey")
    tag_rm_p.add_argument("tags", nargs="+")
    tag_rm_p.add_argument("--bib")
    tag_rm_p.add_argument("--config")
    tag_rm_p.add_argument("--dry-run", action="store_true")
    tag_list_p = tag_sub.add_parser("list")
    tag_list_p.add_argument("citekey", nargs="?")
    tag_list_p.add_argument("--bib")
    tag_list_p.add_argument("--config")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--query")
    search_parser.add_argument("--author")
    search_parser.add_argument("--year", type=int)
    search_parser.add_argument("--tag")
    search_parser.add_argument("--bib")
    search_parser.add_argument("--config")

    bib_parser = subparsers.add_parser("bib")
    bib_sub = bib_parser.add_subparsers(dest="bib_command", required=True)
    bib_list_p = bib_sub.add_parser("list")
    bib_list_p.add_argument("--config")
    bib_set_p = bib_sub.add_parser("set-default")
    bib_set_p.add_argument("name")
    bib_set_p.add_argument("--config")
    bib_update_p = bib_sub.add_parser("update")
    bib_update_p.add_argument("name")
    bib_update_p.add_argument("--config")
    bib_update_p.add_argument("--dry-run", action="store_true")

    bib_promote_p = bib_sub.add_parser("promote")
    bib_promote_p.add_argument("name")
    bib_promote_p.add_argument("--config")
    bib_promote_p.add_argument("--dry-run", action="store_true")
    bib_promote_p.add_argument("--keep-preprint", action="store_true")

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config")

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--config")
    serve_parser.add_argument("--host")
    serve_parser.add_argument("--port", type=int)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--config")
    init_parser.add_argument("--force", action="store_true",
                             help="overwrite existing config")
    init_parser.add_argument("--setup", action="store_true",
                             help="write config, managed services, and browser fallback")
    init_parser.add_argument("--with-services", action="store_true",
                             help="write managed service files")
    init_parser.add_argument("--with-browser", action="store_true",
                             help="configure and install browser fallback")
    init_parser.add_argument("--with-flaresolverr", action="store_true",
                             help="include FlareSolverr service and config")
    init_parser.add_argument("--bib", default="~/bibs/main.bib",
                             help="default BibTeX file path for --setup")
    init_parser.add_argument("--name", default="main",
                             help="default bib name for --setup")

    services_parser = subparsers.add_parser("services")
    services_sub = services_parser.add_subparsers(dest="services_command", required=True)
    for command in ("up", "down", "status"):
        p = services_sub.add_parser(command)
        p.add_argument("--config")

    browser_parser = subparsers.add_parser("browser")
    browser_sub = browser_parser.add_subparsers(dest="browser_command", required=True)
    browser_install = browser_sub.add_parser("install")
    browser_install.add_argument("browser", nargs="?", default="chromium")
    browser_install.add_argument("--config")

    return parser


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
    args = parser.parse_args(list(argv))

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    effective_home = home_dir or os.path.expanduser("~")
    config_path = getattr(args, "config", None) or default_config_path(effective_home)

    if args.command == "add":
        return _run_add(args, home_dir=effective_home, config_path=config_path,
                        stdout=out, stderr=err, fetch_web=fetch_web, fetch_search=fetch_search)
    if args.command == "pdf":
        return _run_pdf_retry(args, home_dir=effective_home, config_path=config_path,
                              stdout=out, stderr=err)
    if args.command == "tag":
        return _run_tag(args, home_dir=effective_home, config_path=config_path,
                        stdout=out, stderr=err)
    if args.command == "search":
        return _run_search(args, home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err)
    if args.command == "bib":
        return _run_bib(args, home_dir=effective_home, config_path=config_path,
                        stdout=out, stderr=err)
    if args.command == "doctor":
        return _run_doctor(home_dir=effective_home, config_path=config_path,
                           stdout=out, stderr=err)
    if args.command == "serve":
        return _run_serve(args, home_dir=effective_home, config_path=config_path,
                          stdout=out, stderr=err)
    if args.command == "init":
        return _run_init(args, config_path=config_path, stdout=out, stderr=err)
    if args.command == "services":
        return _run_services(args, config_path=config_path, stdout=out, stderr=err)
    if args.command == "browser":
        return _run_browser(args, stdout=out, stderr=err)
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
    setup_mode = args.setup or args.with_services or args.with_browser or args.with_flaresolverr
    with_services = args.setup or args.with_services or args.with_flaresolverr
    with_browser = args.setup or args.with_browser

    if setup_mode:
        content = setup_service.render_config(
            bib_name=args.name,
            bib_path=args.bib,
            with_browser=with_browser,
            with_flaresolverr=args.with_flaresolverr,
        )
    else:
        template = importlib.resources.files("pzi").joinpath("config.template.toml")
        with importlib.resources.as_file(template) as src:
            content = Path(src).read_text()
    dest.write_text(content)
    print(f"created {dest}", file=stdout)

    if with_services:
        for path in setup_service.write_service_files(
            str(dest), with_flaresolverr=args.with_flaresolverr
        ):
            print(f"created {path}", file=stdout)

    if with_browser:
        code = setup_service.install_playwright_browser(
            "chromium", stdout=stdout, stderr=stderr
        )
        if code != 0:
            print("browser install failed", file=stderr)
            return code
    return 0


def _run_services(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    return setup_service.run_services_command(
        args.services_command, config_path=config_path, stdout=stdout, stderr=stderr
    )


def _run_browser(args, *, stdout: TextIO, stderr: TextIO) -> int:
    if args.browser_command == "install":
        return setup_service.install_playwright_browser(
            args.browser,
            stdout=stdout,
            stderr=stderr,
        )
    print(f"unknown browser command: {args.browser_command}", file=stderr)
    return 2


def main() -> int:
    return run_cli(sys.argv[1:])


def _run_add(
    args: argparse.Namespace,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    fetch_web=None,
    fetch_search=None,
) -> int:
    record_overrides = _build_record_overrides_from_add_args(args)
    kwargs = {}
    if fetch_web is not None:
        kwargs["fetch_web"] = fetch_web
    if fetch_search is not None:
        kwargs["fetch_search"] = fetch_search
    result = add_input_to_bib(
        config_path=config_path,
        home_dir=home_dir,
        value=args.value,
        record_overrides=record_overrides,
        bib_selector=args.bib,
        dry_run=args.dry_run,
        **kwargs,
    )

    if result["status"] == "error":
        print(result["message"], file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    prefix = "DRY RUN: " if result["dry_run"] else ""
    print(f"{prefix}{result['action']} {result['citekey']} in {result['bib_name']}", file=stdout)
    for warning in result["warnings"]:
        print(f"warning: {warning}", file=stderr)
    return 0


def _run_pdf_retry(args, *, home_dir, config_path, stdout, stderr) -> int:
    if args.pdf_command == "attach":
        result = attach_pdf(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=args.bib,
            citekey=args.citekey,
            source=args.source,
        )
        if result["status"] == "ok":
            print(
                f"attached PDF {result['citekey']} -> {result['local_pdf_path']}",
                file=stdout,
            )
            return 0
        print(result["message"], file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    result = retry_pdf(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=args.bib,
        citekey=args.citekey,
    )
    if result["status"] == "ok":
        print(
            f"fetched PDF {result['citekey']} -> {result['local_pdf_path']}",
            file=stdout,
        )
        return 0
    print(result["message"], file=stderr)
    for error in result["errors"]:
        print(f"- {error}", file=stderr)
    return 1


def _run_tag(args, *, home_dir, config_path, stdout, stderr) -> int:
    if args.tag_command == "list":
        result = list_tags(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=args.bib,
            citekey=args.citekey,
        )
        if result["status"] == "ok":
            for tag in result["tags"]:
                print(tag, file=stdout)
            return 0
        print("failed to list tags", file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    flat_tags: list[str] = []
    for raw in args.tags:
        flat_tags.extend(parse_tag_csv(raw))

    fn = add_tags if args.tag_command == "add" else remove_tags
    result = fn(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=args.bib,
        citekey=args.citekey,
        tags=flat_tags,
        dry_run=args.dry_run,
    )
    if result["status"] == "ok":
        prefix = "DRY RUN: " if result["dry_run"] else ""
        joined = ", ".join(result["tags"]) if result["tags"] else "(none)"
        print(
            f"{prefix}{result['message']} for {result['citekey']}: {joined}",
            file=stdout,
        )
        return 0
    return _render_errors(result["message"], result["errors"], stderr)


def _render_errors(message: str, errors: list[str], stderr: TextIO) -> int:
    print(message, file=stderr)
    for error in errors:
        print(f"- {error}", file=stderr)
    return 1


def _run_search(args, *, home_dir, config_path, stdout, stderr) -> int:
    if not any((args.query, args.author, args.year, args.tag)):
        print("error: at least one of --query, --author, --year, --tag is required", file=stderr)
        return 1

    result = search_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=args.bib,
        query=args.query,
        author=args.author,
        year=args.year,
        tag=args.tag,
    )
    if result["status"] == "ok":
        for match in result["matches"]:
            title = match["title"] or ""
            year = match["year"] if match["year"] is not None else ""
            fields = ",".join(match["matched_fields"])
            print(f"{match['citekey']}\t{year}\t{title}\t[{fields}]", file=stdout)
        if not result["matches"]:
            print("no matches", file=stdout)
        return 0
    print("search failed", file=stderr)
    for error in result["errors"]:
        print(f"- {error}", file=stderr)
    return 1


def _run_bib(args, *, home_dir, config_path, stdout, stderr) -> int:
    if args.bib_command == "list":
        result = list_bibs(config_path=config_path, home_dir=home_dir)
        if result["status"] == "ok":
            for bib in result["bibs"]:
                marker = " (default)" if bib["default"] else ""
                print(f"{bib['name']}\t{bib['path']}{marker}", file=stdout)
            return 0
        print("failed to list bibs", file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    if args.bib_command == "set-default":
        result = set_default_bib(
            config_path=config_path, home_dir=home_dir, name=args.name
        )
        if result["status"] == "ok":
            print(result["message"], file=stdout)
            return 0
        print(result["message"], file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    if args.bib_command == "update":
        result = update_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=args.name,
            dry_run=args.dry_run,
        )
        if result["status"] == "ok":
            prefix = "DRY RUN: " if result["dry_run"] else ""
            for item in result["items"]:
                changed = ", ".join(item["changed_fields"]) or "(no-op)"
                note = f" [{item['note']}]" if item["note"] else ""
                print(f"{prefix}{item['citekey']}: {changed}{note}", file=stdout)
            if not result["items"]:
                print(f"{prefix}no updates", file=stdout)
            return 0
        print("update failed", file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    if args.bib_command == "promote":
        result = promote_bib(
            config_path=config_path,
            home_dir=home_dir,
            bib_selector=args.name,
            dry_run=args.dry_run,
            keep_preprint=args.keep_preprint,
        )
        if result["status"] == "ok":
            prefix = "DRY RUN: " if result["dry_run"] else ""
            for item in result["items"]:
                changed = ", ".join(item["changed_fields"]) or "(no-op)"
                note = f" [{item['note']}]" if item["note"] else ""
                pdf = " [PDF]" if item["pdf_attached"] else ""
                pub = item["published_citekey"] or item["preprint_citekey"]
                print(
                    f"{prefix}{item['preprint_citekey']} -> {pub}: {changed}{pdf}{note}",
                    file=stdout,
                )
            if not result["items"]:
                print(f"{prefix}no preprints to promote", file=stdout)
            return 0
        print("promote failed", file=stderr)
        for error in result["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    print(f"unknown bib command: {args.bib_command}", file=stderr)
    return 2


def _run_doctor(*, home_dir, config_path, stdout, stderr) -> int:
    result = doctor_check(config_path=config_path, home_dir=home_dir)
    print(json.dumps(result, indent=2, default=str), file=stdout)
    return 0 if result["config_ok"] else 1


def _run_serve(args, *, home_dir, config_path, stdout, stderr) -> int:
    from pzi.config import load_config_file
    from pzi.http_api import DEFAULT_MAX_BODY_BYTES, build_http_security_config, run_server

    host = args.host
    port = args.port
    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]
    if config is None and (host is None or port is None):
        print("failed to load config", file=stderr)
        for error in cfg["errors"]:
            print(f"- {error}", file=stderr)
        return 1

    if config is not None:
# pragma: no cover — covered by integration/browser tests
        host = host or config["api_listen_host"]  # pragma: no cover
# pragma: no cover — covered by integration/browser tests
        port = port or config["api_listen_port"]  # pragma: no cover

    security = build_http_security_config(
        auth_token=config.get("api_auth_token") if config is not None else None,
        allowed_origins=config.get("api_allowed_origins") if config is not None else None,
        max_body_bytes=config.get("api_max_body_bytes", DEFAULT_MAX_BODY_BYTES)
        if config is not None
        else DEFAULT_MAX_BODY_BYTES,
    )

    print(f"serving on {host}:{port}", file=stdout)
    stdout.flush()
    run_server(
        config_path=config_path,
        home_dir=home_dir,
        host=host,
        port=port,
        security=security,
    )
    return 0


def _build_record_overrides_from_add_args(
    args: argparse.Namespace,
) -> dict[str, object]:
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


if __name__ == "__main__":
    raise SystemExit(main())
