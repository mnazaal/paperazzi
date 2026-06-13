"""CLI command runners for pzi — one function per subcommand.

Each ``_run_*`` function takes an argparse Namespace + config/home_dir and
returns an int exit code.  All I/O goes through the injected ``stdout`` /
``stderr`` file handles.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TextIO

from pzi import setup_service
from pzi.bib_service import bib_stats, delete_entry, list_entries
from pzi.capture_core import capture_to_bib
from pzi.cli_parser import (
    build_capture_input_from_add_args,
    build_capture_options_from_add_args,
)
from pzi.cli_render import (
    _error_lines,
    _render_add_success,
    _render_bib_stats,
    _render_clean_result,
    _render_dedupe_result,
    _render_delete_success,
    _render_reindex_result,
)
from pzi.cli_server import build_server_plan
from pzi.commands.pdf import run_pdf_command
from pzi.commands.promote import run_promote_command
from pzi.commands.search import run_search_command
from pzi.commands.tags import run_tag_command
from pzi.commands.update import run_update_command
from pzi.config import load_config_file, resolve_library_target
from pzi.doctor_service import doctor_check
from pzi.http_api import run_server
from pzi.pdf_service import attach_pdf, retry_failed_pdfs, retry_pdf
from pzi.promote_service import promote_bib
from pzi.search_service import search_bib
from pzi.tag_service import add_tags, list_tags, parse_tag_csv, remove_tags
from pzi.update_service import update_bib


def _print_lines(lines: Sequence[str], out: TextIO) -> None:
    for line in lines:
        print(line, file=out)


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
        # No install side effects: the translation-server is installed and
        # started on first use of `pzi server` / `pzi add` (see backend_session).
        print(
            "next: run `pzi server` (or `pzi add <doi|url|pdf>`) — the "
            "translation-server installs and starts on first use.",
            file=stdout,
        )
    if with_browser:
        print(
            "for the browser PDF fallback, install Playwright once: "
            "`playwright install chromium` (it also installs on first use).",
            file=stdout,
        )
    return 0


def _run_services(args, *, config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    from pathlib import Path

    from pzi.ts_backend import ensure_node, is_ts_reachable
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
    action = args.services_command

    if action == "status":
        if is_ts_reachable(ts_url):
            print(f"translation-server is running at {ts_url}", file=stdout)
        else:
            print("translation-server is not running (start it with `pzi server`)",
                  file=stdout)
        return 0
    elif action == "update":
        # Re-download/reinstall the translation-server on disk.
        print("reinstalling translation-server …", file=stdout)
        node = ensure_node(data_home, interactive=True, stdout=stdout, stderr=stderr)
        if node is None:
            return 1
        # Remove existing install to trigger full re-clone.
        ts_dir = data_home / "ts"
        if ts_dir.exists():
            if is_ts_reachable(ts_url):
                print(
                    "warning: a translation-server is running; restart `pzi server` "
                    "after the update to use the new version.",
                    file=stderr,
                )
            import shutil
            shutil.rmtree(ts_dir, ignore_errors=True)
        result = ensure_ts(data_home, node, stdout=stdout, stderr=stderr)
        if result is None:
            return 1
        print("translation-server reinstalled. Run `pzi server` to start.", file=stdout)
        return 0
    else:
        print(f"unknown services command: {action}", file=stderr)
        return 2


def _run_config(args, *, home_dir, config_path, stdout, stderr) -> int:
    if args.config_command == "validate":
        result = load_config_file(config_path, home_dir=home_dir)
        if result["config"] is not None:
            print(f"config valid: {result['path']}", file=stdout)
            return 0
        _print_lines(_error_lines("config invalid", result["errors"]), stderr)
        return 1
    print(f"unknown config command: {args.config_command}", file=stderr)
    return 2


def _run_bib_stats(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
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
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        return 0 if result["status"] == "ok" else 1
    if result["status"] == "ok":
        _print_lines(_render_bib_stats(result), stdout)
        return 0
    _print_lines(_error_lines("bib-stats failed", result["errors"]), stderr)
    return 1


def _run_clean(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    from pzi.clean_service import clean_library, validate_library

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

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        if result["status"] != "ok":
            return 1
        return 0 if not result.get("issues") else 1

    if result["status"] != "ok":
        _print_lines(_error_lines("clean failed", [result.get("message", "")]), stderr)
        return 1

    _print_lines(_render_clean_result(result, dry_run=args.dry_run or not args.fix), stdout)
    return 0 if not result.get("issues") else 1


def _run_dedupe(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
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
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        return 0 if result.get("total_clusters", 0) == 0 else 1
    _print_lines(_render_dedupe_result(result), stdout)
    return 0 if result.get("total_clusters", 0) == 0 else 1


def _run_merge(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
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


def _run_export(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
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
    from pzi.cli import run_cli

    return run_cli(sys.argv[1:])


def _run_entries(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:

    result = list_entries(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        offset=max(0, args.offset),
        limit=max(1, min(args.limit, 500)),
        sort=args.sort,
    )
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, default=str), file=stdout)
        return 0 if result["status"] == "ok" else 1
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

    cfg = load_config_file(config_path, home_dir=home_dir)
    config = cfg["config"]

    # When the caller injects fetch functions (tests) or there is no config,
    # skip the backend entirely.  Otherwise run capture inside a backend session
    # that reuses a running `pzi server` or, failing that, spawns an ephemeral
    # translation-server child that is torn down when this command exits.
    if config is not None and fetch_web is None and fetch_search is None:
        from pzi.ts_backend import backend_session

        with backend_session(
            config, config_path, home_dir,
            interactive=True, stdout=stdout, stderr=stderr,
        ) as backend:
            if not backend["ready"]:
                print(
                    "translation server is not running — cannot add paper.\n"
                    "  Run 'pzi server' (it starts the translation-server), then retry.",
                    file=stderr,
                )
                return 1
            return _capture_and_render(
                args, cfg, home_dir=home_dir, config_path=config_path,
                stdout=stdout, stderr=stderr, bib_selector=bib_selector,
                fetch_web=fetch_web, fetch_search=fetch_search,
            )

    return _capture_and_render(
        args, cfg, home_dir=home_dir, config_path=config_path,
        stdout=stdout, stderr=stderr, bib_selector=bib_selector,
        fetch_web=fetch_web, fetch_search=fetch_search,
    )


def _capture_and_render(
    args: argparse.Namespace,
    cfg,
    *,
    home_dir: str,
    config_path: str,
    stdout: TextIO,
    stderr: TextIO,
    bib_selector: str | None,
    fetch_web=None,
    fetch_search=None,
) -> int:
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
    suggestion = result.get("pdf_suggestion")
    if isinstance(suggestion, str) and suggestion:
        print(f"suggestion: {suggestion}", file=stderr)
    return 0


def _run_pdf_retry(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    return run_pdf_command(
        args,
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        stdout=stdout,
        stderr=stderr,
        attach_pdf_fn=attach_pdf,
        retry_pdf_fn=retry_pdf,
        retry_failed_pdfs_fn=retry_failed_pdfs,
    )


def _run_pdf_retry_failed_only(
    *, config_path: str, home_dir: str, bib_selector: str | None,
    stdout: TextIO, stderr: TextIO,
) -> int:
    args = argparse.Namespace(pdf_command="retry", citekey=None, failed_only=True)
    return run_pdf_command(
        args,
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        stdout=stdout,
        stderr=stderr,
        attach_pdf_fn=attach_pdf,
        retry_pdf_fn=retry_pdf,
        retry_failed_pdfs_fn=retry_failed_pdfs,
    )


def _run_tag(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    return run_tag_command(
        args,
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        stdout=stdout,
        stderr=stderr,
        list_tags_fn=list_tags,
        add_tags_fn=add_tags,
        remove_tags_fn=remove_tags,
        parse_tag_csv_fn=parse_tag_csv,
    )


def _render_errors(message: str, errors: list[str], stderr: TextIO) -> int:
    _print_lines(_error_lines(message, errors), stderr)
    return 1


def _run_search(args, *, home_dir, config_path, stdout, stderr, bib_selector) -> int:
    return run_search_command(
        args,
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
        stdout=stdout,
        stderr=stderr,
        search_bib_fn=search_bib,
    )


def _run_update(args, *, home_dir, config_path, stdout, stderr) -> int:
    return run_update_command(
        args,
        config_path=config_path,
        home_dir=home_dir,
        stdout=stdout,
        stderr=stderr,
        update_bib_fn=update_bib,
    )


def _run_promote(args, *, home_dir, config_path, stdout, stderr) -> int:
    return run_promote_command(
        args,
        config_path=config_path,
        home_dir=home_dir,
        stdout=stdout,
        stderr=stderr,
        promote_bib_fn=promote_bib,
    )


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
    stop_after = getattr(args, "stop_after", None)

    def _serve() -> None:
        print(f"serving on {host}:{port}", file=stdout)
        stdout.flush()
        run_server(
            config_path=config_path,
            home_dir=home_dir,
            host=host,
            port=port,
            security=plan["security"],
            idle_minutes=stop_after,
            browser_profile_path=config.get("browser_profile_path") if config else None,
            browser_engine=config.get("browser_engine", "chromium") if config else "chromium",
        )

    if config is None:
        with _sigterm_as_keyboard_interrupt():
            _serve()
        return 0

    # Run the HTTP API for the lifetime of a backend session.  The session
    # reuses a running translation-server or starts one as a bound child; the
    # child is torn down when this process exits (Ctrl-C, SIGTERM, or the
    # --stop-after idle timeout), so the backend never outlives `pzi server`.
    from pzi.ts_backend import backend_session

    with _sigterm_as_keyboard_interrupt(), backend_session(
        config, config_path, home_dir,
        interactive=True, stdout=stdout, stderr=stderr,
    ) as backend:
        if not backend["ready"]:
            print(
                "warning: translation server is not running — "
                "capture requests will fail until it is ready",
                file=stderr,
            )
        _serve()
    return 0


@contextmanager
def _sigterm_as_keyboard_interrupt() -> Iterator[None]:
    """Translate SIGTERM into KeyboardInterrupt for the duration of the block.

    The stdlib HTTP server stops cleanly on KeyboardInterrupt (Ctrl-C), running
    enclosing ``finally`` blocks; raw SIGTERM would otherwise kill the process
    without unwinding, leaking the translation-server child.  Restores the
    previous handler on exit.  No-op off the main thread, where signal handlers
    cannot be installed.
    """
    def _raise(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, _raise)
    except (ValueError, OSError, AttributeError):
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


# ---------------------------------------------------------------------------
