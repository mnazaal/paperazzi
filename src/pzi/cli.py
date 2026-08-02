"""CLI entrypoints for pzi."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TextIO, TypedDict

from pzi import cli_json, exit_codes
from pzi.bib_repository import ConcurrentEditError
from pzi.cli_parser import build_parser, set_error_stream
from pzi.commands.add import run_add_command as _run_add
from pzi.commands.check import run_check_command as _run_check
from pzi.commands.common import command_label
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


@dataclass(frozen=True)
class _DispatchContext:
    """Everything the command runners need, gathered once by :func:`run_cli`."""

    args: Any
    cfg: _CommonRunKwargs
    out: TextIO
    err: TextIO
    effective_home: str
    config_path: str
    bib_selector: BibSelector
    # Single-target commands (e.g. `add`) only ever parse a scalar --target.
    single_selector: str | None
    fetch_web: Any
    fetch_search: Any


# The dispatch table lives at module scope so it can be checked against the
# parser. It used to be a local inside `run_cli`, with `CLI_COMMANDS` a
# hand-maintained literal standing in for it — so the test named "dispatch
# registry covers all parser commands" actually compared the parser against
# that literal, and a command added to both the parser and the literal but not
# to the dispatch dict passed CI and then failed at runtime with
# "unknown command".
_DISPATCH: dict[str, Callable[[_DispatchContext], int]] = {
    "add": lambda c: _run_add(
        c.args, **c.cfg,
        stdout=c.out, stderr=c.err, bib_selector=c.single_selector,
        fetch_web=c.fetch_web, fetch_search=c.fetch_search,
    ),
    "check": lambda c: _run_check(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.single_selector,
    ),
    "delete": lambda c: _run_delete(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.bib_selector,
    ),
    "doctor": lambda c: _run_doctor(
        c.args, home_dir=c.effective_home, config_path=c.config_path,
        stdout=c.out, stderr=c.err,
    ),
    "entries": lambda c: _run_entries(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.bib_selector,
    ),
    "export": lambda c: _run_export(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.bib_selector,
    ),
    "fix": lambda c: _run_fix(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.single_selector,
    ),
    "inbox": lambda c: _run_inbox(c.args, **c.cfg, stdout=c.out, stderr=c.err),
    "import": lambda c: _run_import(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.bib_selector,
    ),
    "init": lambda c: _run_init(
        c.args, home_dir=c.effective_home, config_path=c.config_path,
        stdout=c.out, stderr=c.err,
    ),
    "pdf": lambda c: _run_pdf(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.single_selector,
    ),
    "search": lambda c: _run_search(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.bib_selector,
    ),
    "server": lambda c: _run_server(
        c.args, **c.cfg, stdout=c.out, stderr=c.err,
    ),
    "tag": lambda c: _run_tag(
        c.args, **c.cfg, stdout=c.out, stderr=c.err, bib_selector=c.single_selector,
    ),
    "update": lambda c: _run_update(
        c.args, **c.cfg, stdout=c.out, stderr=c.err,
    ),
}

#: Derived from the dispatch table, never hand-written.
CLI_COMMANDS: tuple[str, ...] = tuple(sorted(_DISPATCH))


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
    # Precedence: --config flag, then PZI_CONFIG, then the XDG default. The env
    # var is what lets a cron job or systemd unit point at an alternate library
    # without threading --config through every invocation.
    config_path: str = (
        getattr(args, "config", None)
        or getattr(args, "top_level_config", None)
        or os.environ.get("PZI_CONFIG")
        or default_config_path(effective_home)
    )

    if args.command is None:
        parser.print_help(file=out)
        return 0

    _cfg: _CommonRunKwargs = {"home_dir": effective_home, "config_path": config_path}
    _bib_selector: BibSelector = getattr(args, "target", None)

    ctx = _DispatchContext(
        args=args,
        cfg=_cfg,
        out=out,
        err=err,
        effective_home=effective_home,
        config_path=config_path,
        bib_selector=_bib_selector,
        single_selector=_bib_selector if isinstance(_bib_selector, str) else None,
        fetch_web=fetch_web,
        fetch_search=fetch_search,
    )

    if args.command in _DISPATCH:
        # `--json` promises exactly one document on stdout *including when the
        # command fails*, but these handlers are the last thing to run and used
        # to report only as prose on stderr. `resolve_target` alone raises
        # `PziError` as the first statement of seven runners, so a bad
        # `--target` produced no JSON for any of them.
        as_json = getattr(args, "json", False)

        def _fail(message: str, details: Sequence[str], code: int) -> int:
            if as_json:
                # `.errors[]` is the documented failure channel, so it must say
                # something on every failure. Most `PziError`s carry details
                # (the per-line config errors); the ones that do not put
                # everything in the message.
                cli_json.emit_error(
                    message,
                    list(details) or [message],
                    out,
                    command=command_label(args),
                )
                return code
            print(f"error: {message}", file=err)
            for detail in details:
                print(f"  - {detail}", file=err)
            return code

        try:
            return _DISPATCH[args.command](ctx)
        except BrokenPipeError:
            # A downstream reader (e.g. `| head`) closed the pipe — let main()
            # handle it quietly; never report it as a command error.
            raise
        except ConcurrentEditError:
            # Another process edited the bib between our pre-lock snapshot and
            # acquiring the lock; the write was aborted to prevent data loss.
            # A retry almost always succeeds (the race window is tiny).
            return _fail(
                "bib file was modified externally while writing — "
                "retry the command",
                [],
                exit_codes.ENVIRONMENT,
            )
        except PziError as exc:
            # Carries a message already phrased for the user (e.g. naming the
            # bib file that is not valid UTF-8) and the exit code to report.
            return _fail(exc.message, exc.details, exc.code)
        except (OSError, UnicodeDecodeError) as exc:
            # Expected environmental failures (permission denied, disk full, a
            # file that is not valid UTF-8, …) become a clean diagnostic
            # instead of a raw traceback.  Genuine bugs still propagate.
            return _fail(_friendly_error(exc), [], exit_codes.ENVIRONMENT)

    print(f"unknown command: {args.command}", file=err)
    return exit_codes.USAGE


def main() -> int:
    try:
        return run_cli(sys.argv[1:])
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return exit_codes.INTERRUPTED
    except BrokenPipeError:
        # Output consumer closed the pipe (e.g. `pzi entries | head`).  Redirect
        # stdout to devnull so the interpreter's final flush cannot re-raise on
        # shutdown, then exit with the conventional 128 + SIGPIPE(13) status.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass  # stdout may have no real fd (e.g. already closed, or captured)
        return exit_codes.BROKEN_PIPE


if __name__ == "__main__":
    raise SystemExit(main())
