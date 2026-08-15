"""The CLI shape, pinned — item 420.

1.0 freezes command and flag names, so they need a test that fails when they
change. Nothing asserted the set before this: `build_parser()` builds two dozen
parsers, and a renamed flag, a dropped subcommand or a changed default passed
every gate.

**Shape, not help text.** The obvious version of this test snapshots
``--help`` output, and it would be worse than nothing here: argparse wraps to
terminal width and its formatter has changed between Python versions, while CI
runs 3.11/3.12/3.13 across two operating systems. It would also fail whenever
someone rewords a help string, which is not a contract change — and a test that
cries wolf is regenerated without being read. So this records flags,
positionals, ``dest``, ``nargs``, ``choices``, ``required`` and defaults, and
deliberately ignores prose.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pzi.cli_parser import build_parser

SNAPSHOT = Path(__file__).parent / "fixtures" / "cli_surface.txt"

#: Set to regenerate after an *intended* CLI change.
UPDATE_ENV = "PZI_UPDATE_CLI_SURFACE"


def _format_default(action: argparse.Action) -> str:
    """The default, when there is one worth pinning.

    ``None`` and ``False`` are argparse's own "nothing was set" values and say
    nothing about the contract; a real default (a port, a limit, a format) is
    exactly the kind of silent change this file exists to catch.
    """
    if action.default is None or action.default is False:
        return ""
    if action.default is argparse.SUPPRESS:
        return ""
    return f" default={action.default!r}"


def _describe_action(action: argparse.Action) -> str | None:
    """One stable line for one argument, or None for the parser plumbing."""
    if isinstance(action, argparse._SubParsersAction | argparse._HelpAction):
        return None
    if action.option_strings:
        name = " ".join(sorted(action.option_strings))
    else:
        name = f"<{action.dest}>"
    parts = [name, f"dest={action.dest}"]
    # The action class, when it is not argparse's default `store`. Omitted
    # before, which made a `store` -> `append` change invisible to this
    # snapshot — and that is a semantic change to how a flag may be spelled,
    # exactly what the file exists to catch. Found by making one.
    action_name = type(action).__name__
    if action_name not in ("_StoreAction", "_StoreTrueAction"):
        parts.append(f"action={action_name.removeprefix('_').removesuffix('Action').lower()}")
    if action.nargs is not None:
        parts.append(f"nargs={action.nargs!r}")
    if action.choices is not None:
        parts.append(f"choices={sorted(map(str, action.choices))}")
    if action.required:
        parts.append("required")
    return "    " + " ".join(parts) + _format_default(action)


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def describe_parser(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()) -> list[str]:
    """The full command tree as sorted, diffable lines."""
    label = " ".join(("pzi", *path))
    lines = [label]
    lines.extend(
        sorted(
            line
            for line in (_describe_action(action) for action in parser._actions)
            if line is not None
        )
    )
    for name, subparser in sorted(_subparsers(parser).items()):
        lines.append("")
        lines.extend(describe_parser(subparser, (*path, name)))
    return lines


def render_surface() -> str:
    return "\n".join(describe_parser(build_parser())) + "\n"


def test_the_cli_surface_matches_its_snapshot() -> None:
    """Fails on any change to the command tree, a flag name, or a default.

    If this failed because of a change you meant to make, that change is a CLI
    contract change: record it in `CHANGELOG.md`, then regenerate with

        PZI_UPDATE_CLI_SURFACE=1 pytest tests/test_cli_surface.py

    Read the diff before you do. Regenerating without reading turns this file
    into a record of whatever happened rather than of what was decided.
    """
    current = render_surface()
    if os.environ.get(UPDATE_ENV) == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
        return
    assert SNAPSHOT.exists(), (
        f"{SNAPSHOT} is missing — regenerate with {UPDATE_ENV}=1 pytest {__file__}"
    )
    recorded = SNAPSHOT.read_text(encoding="utf-8")
    assert current == recorded, (
        "the CLI surface changed. This is a frozen contract at 1.0: a renamed "
        "flag, a dropped subcommand or a changed default breaks callers.\n"
        "If the change is intended, record it in CHANGELOG.md and regenerate:\n"
        f"    {UPDATE_ENV}=1 pytest {__file__}\n"
    )


def test_every_command_and_subcommand_appears_in_the_snapshot() -> None:
    """A guard on the walk itself.

    If `describe_parser` ever stopped descending — a refactor to a different
    subparser mechanism, say — the snapshot would keep matching while covering
    a fraction of the CLI, and the freeze would quietly stop meaning anything.
    """
    parser = build_parser()
    commands = set(_subparsers(parser))
    assert commands, "no subcommands found — the parser walk is broken"

    recorded = SNAPSHOT.read_text(encoding="utf-8")
    for command in commands:
        assert f"\npzi {command}\n" in recorded, f"`pzi {command}` is not pinned"

    # And the nested ones, which are the easiest to lose in a refactor.
    for parent in ("pdf", "tag", "fix"):
        for child in _subparsers(_subparsers(parser)[parent]):
            assert f"\npzi {parent} {child}\n" in recorded, (
                f"`pzi {parent} {child}` is not pinned"
            )
