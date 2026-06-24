"""CLI entrypoints for pzi."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from typing import TextIO, TypedDict

from pzi.cli_parser import build_parser
from pzi.commands.add import run_add_command as _run_add
from pzi.commands.delete import run_delete_command as _run_delete
from pzi.commands.doctor import run_doctor_command as _run_doctor
from pzi.commands.entries import run_entries_command as _run_entries
from pzi.commands.export import run_export_command as _run_export
from pzi.commands.fix import run_fix_command as _run_fix
from pzi.commands.import_ import run_import_command as _run_import
from pzi.commands.init import run_init_command as _run_init
from pzi.commands.pdf import run_pdf_command as _run_pdf
from pzi.commands.search import run_search_command as _run_search
from pzi.commands.server import run_server_command as _run_server
from pzi.commands.tags import run_tag_command as _run_tag
from pzi.commands.update import run_update_command as _run_update
from pzi.config import default_config_path

BibSelector = str | Sequence[str] | None


class _CommonRunKwargs(TypedDict):
    """Shared config/home keywords splatted into command runners."""

    home_dir: str
    config_path: str


CLI_COMMANDS: tuple[str, ...] = (
    "add",
    "delete",
    "doctor",
    "entries",
    "export",
    "fix",
    "import",
    "init",
    "pdf",
    "search",
    "server",
    "tag",
    "update",
)


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

    _cfg: _CommonRunKwargs = {"home_dir": effective_home, "config_path": config_path}
    _bib_selector: BibSelector = getattr(args, "target", None)
    # Single-target commands (e.g. `add`) only ever parse a scalar --target.
    _single_selector: str | None = _bib_selector if isinstance(_bib_selector, str) else None

    _dispatch: dict[str, Callable[[], int]] = {
        "add": lambda: _run_add(
            args, **_cfg,
            stdout=out, stderr=err, bib_selector=_single_selector,
            fetch_web=fetch_web, fetch_search=fetch_search,
        ),
        "delete": lambda: _run_delete(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "doctor": lambda: _run_doctor(
            args, home_dir=effective_home, config_path=config_path, stdout=out, stderr=err,
        ),
        "entries": lambda: _run_entries(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "export": lambda: _run_export(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "fix": lambda: _run_fix(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_single_selector,
        ),
        "import": lambda: _run_import(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "init": lambda: _run_init(
            args, config_path=config_path, stdout=out, stderr=err,
        ),
        "pdf": lambda: _run_pdf(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_single_selector,
        ),
        "search": lambda: _run_search(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "server": lambda: _run_server(
            args, **_cfg, stdout=out, stderr=err,
        ),
        "tag": lambda: _run_tag(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_single_selector,
        ),
        "update": lambda: _run_update(
            args, **_cfg, stdout=out, stderr=err,
        ),
    }

    if args.command in _dispatch:
        return _dispatch[args.command]()

    print(f"unknown command: {args.command}", file=err)
    return 2


def main() -> int:
    return run_cli(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
