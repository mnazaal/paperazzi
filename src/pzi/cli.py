"""CLI entrypoints for pzi."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from pzi import cli_version_text
from pzi.bib_service import list_bibs, set_default_bib
from pzi.cli_commands import (
    _run_add,
    _run_bib_stats,
    _run_clean,
    _run_config,
    _run_dedupe,
    _run_delete,
    _run_detail,
    _run_doctor,
    _run_entries,
    _run_export,
    _run_import,
    _run_init,
    _run_merge,
    _run_pdf_retry,
    _run_promote,
    _run_reindex,
    _run_search,
    _run_server,
    _run_services,
    _run_tag,
    _run_update,
)
from pzi.cli_parser import build_parser
from pzi.cli_render import (
    _error_lines,
    _render_bib_list,
)
from pzi.config import default_config_path


def _print_lines(lines: Sequence[str], out: TextIO) -> None:
    for line in lines:
        print(line, file=out)


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
        if getattr(args, "json", False):
            print(json.dumps(result, indent=2, default=str), file=out)
            return 0 if result["status"] == "ok" else 1
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


def main() -> int:
    return run_cli(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
