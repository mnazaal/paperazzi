"""CLI entrypoints for pzi."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from typing import TextIO, TypedDict

from pzi.bib_repository import ConcurrentEditError
from pzi.cli_parser import build_parser, set_error_stream
from pzi.commands.add import run_add_command as _run_add
from pzi.commands.check import run_check_command as _run_check
from pzi.commands.delete import run_delete_command as _run_delete
from pzi.commands.doctor import run_doctor_command as _run_doctor
from pzi.commands.entries import run_entries_command as _run_entries
from pzi.commands.export import run_export_command as _run_export
from pzi.commands.fix import run_fix_command as _run_fix
from pzi.commands.import_ import run_import_command as _run_import
from pzi.commands.inbox import run_inbox_command as _run_inbox
from pzi.commands.init import run_init_command as _run_init
from pzi.commands.pdf import run_pdf_command as _run_pdf
from pzi.commands.search import run_search_command as _run_search
from pzi.commands.server import run_server_command as _run_server
from pzi.commands.tags import run_tag_command as _run_tag
from pzi.commands.update import run_update_command as _run_update
from pzi.config import default_config_path
from pzi.errors import PziError

BibSelector = str | Sequence[str] | None


class _CommonRunKwargs(TypedDict):
    """Shared config/home keywords splatted into command runners."""

    home_dir: str
    config_path: str


CLI_COMMANDS: tuple[str, ...] = (
    "add",
    "check",
    "delete",
    "doctor",
    "entries",
    "export",
    "fix",
    "inbox",
    "import",
    "init",
    "pdf",
    "search",
    "server",
    "tag",
    "update",
)


def _friendly_error(exc: OSError | UnicodeDecodeError) -> str:
    """Render an expected runtime failure as a concise, human-readable message.

    Avoids the noisy ``[Errno N]`` prefix and the cryptic codec dump that
    ``str(exc)`` produces, preferring the OS message plus the offending path.
    """
    if isinstance(exc, UnicodeDecodeError):
        return f"file is not valid {exc.encoding.upper()} text"
    detail = exc.strerror or str(exc)
    if exc.filename:
        return f"{detail}: {exc.filename}"
    return detail


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
    # Route argparse's own errors through the injected stderr so bad-invocation
    # diagnostics share the stream (and format) used by the command runners.
    set_error_stream(parser, err)

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
        "check": lambda: _run_check(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_single_selector,
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
        "inbox": lambda: _run_inbox(args, **_cfg, stdout=out, stderr=err),
        "import": lambda: _run_import(
            args, **_cfg, stdout=out, stderr=err, bib_selector=_bib_selector,
        ),
        "init": lambda: _run_init(
            args, home_dir=effective_home, config_path=config_path, stdout=out, stderr=err,
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
        try:
            return _dispatch[args.command]()
        except BrokenPipeError:
            # A downstream reader (e.g. `| head`) closed the pipe — let main()
            # handle it quietly; never report it as a command error.
            raise
        except ConcurrentEditError:
            # Another process edited the bib between our pre-lock snapshot and
            # acquiring the lock; the write was aborted to prevent data loss.
            # A retry almost always succeeds (the race window is tiny).
            print(
                "error: bib file was modified externally while writing — "
                "retry the command",
                file=err,
            )
            return 1
        except PziError as exc:
            # Carries a message already phrased for the user (e.g. naming the
            # bib file that is not valid UTF-8).
            print(f"error: {exc}", file=err)
            return 1
        except (OSError, UnicodeDecodeError) as exc:
            # Expected environmental failures (permission denied, disk full, a
            # file that is not valid UTF-8, …) become a clean diagnostic
            # instead of a raw traceback.  Genuine bugs still propagate.
            print(f"error: {_friendly_error(exc)}", file=err)
            return 1

    print(f"unknown command: {args.command}", file=err)
    return 2


def main() -> int:
    try:
        return run_cli(sys.argv[1:])
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Output consumer closed the pipe (e.g. `pzi entries | head`).  Redirect
        # stdout to devnull so the interpreter's final flush cannot re-raise on
        # shutdown, then exit with the conventional 128 + SIGPIPE(13) status.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass  # stdout may have no real fd (e.g. already closed, or captured)
        return 141


if __name__ == "__main__":
    raise SystemExit(main())
