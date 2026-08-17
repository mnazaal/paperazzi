"""The installed console script, exercised as a user runs it.

Every other CLI test either calls a runner directly with injected services, or
spawns `python -c "from pzi.cli import run_cli"`. Both skip the thing that
actually ships: the `pzi = "pzi.cli:main"` console entry point in
`pyproject.toml`. What only this level catches is a command that dispatches but
whose flag was never registered, an entry point that no longer resolves, and an
import-time failure — none of which a runner test can see, because a runner test
supplies the parsed `args` itself.

Kept cheap: one `--help` per command plus a handful of behaviour checks, all
with `PZI_SKIP_AUTO_START=1` so nothing starts a backend.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: The console script beside the interpreter running the tests. `shutil.which`
#: as a fallback so a `pip install -e .` into an active venv also works.
_CONSOLE_SCRIPT = Path(sys.executable).parent / "pzi"
if not _CONSOLE_SCRIPT.exists():
    _found = shutil.which("pzi")
    _CONSOLE_SCRIPT = Path(_found) if _found else _CONSOLE_SCRIPT

pytestmark = pytest.mark.skipif(
    not _CONSOLE_SCRIPT.exists(),
    reason="no installed `pzi` console script beside this interpreter",
)

#: Every top-level command, from `pzi.cli._DISPATCH`. Hardcoded rather than
#: imported so that a command silently disappearing from the dispatch table is a
#: failure here rather than an empty parametrization that passes.
_COMMANDS = [
    "add", "inbox", "pdf", "tag", "search", "library", "update", "doctor",
    "server", "init", "delete", "entries", "export", "import",
]


def _run(argv: list[str], *, home: Path, timeout: int = 60):
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PZI_SKIP_AUTO_START": "1",
    }
    return subprocess.run(
        [str(_CONSOLE_SCRIPT), *argv],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A home, a config and a one-entry library."""
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".local" / "share").mkdir(parents=True)
    bib = tmp_path / "ml.bib"
    bib.write_text(
        "@article{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "}\n"
    )
    papers = tmp_path / "papers"
    papers.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n'
    )
    return home, config, bib


def test_the_console_script_resolves_and_reports_a_version(tmp_path: Path) -> None:
    """The entry point itself: `pzi.cli:main` must import and run."""
    result = _run(["--version"], home=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


@pytest.mark.parametrize("command", _COMMANDS)
def test_every_command_has_working_help(command: str, tmp_path: Path) -> None:
    """`--help` builds that command's parser, so a malformed one fails here.

    It also proves the command is reachable through the installed script rather
    than only through `_DISPATCH`.
    """
    result = _run([command, "--help"], home=tmp_path)

    assert result.returncode == 0, f"{command}: {result.stderr}"
    assert command.split()[0] in result.stdout


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["entries"], 0),
        (["entries", "--stats"], 0),
        (["entries", "smith2024graph"], 0),
        (["search", "--query", "graph"], 0),
        (["search", "--query", "nothing-matches-this"], 1),
        (["entries", "nosuchkey"], 3),
        (["search"], 2),
        (["entries", "--target", "/nonexistent.bib"], 5),
    ],
)
def test_documented_exit_codes_through_the_installed_script(
    argv: list[str], expected: int, library: tuple[Path, Path, Path]
) -> None:
    """The exit codes README documents, produced by the shipped executable.

    A runner test cannot show this: it returns an int to the test rather than to
    a shell, so nothing checks that the int survives `main`'s own handling.
    """
    home, config, _bib = library

    result = _run([*argv, "--config", str(config)], home=home)

    assert result.returncode == expected, (
        f"{argv} exited {result.returncode}, expected {expected}\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["entries"],
        ["entries", "--stats"],
        ["search", "--query", "graph"],
        ["tag", "list"],
        ["library", "dedupe"],
        ["library", "clean"],
    ],
)
def test_json_mode_emits_exactly_one_document(
    argv: list[str], library: tuple[Path, Path, Path]
) -> None:
    """One parseable document on stdout, and nothing else on it."""
    home, config, _bib = library

    result = _run([*argv, "--json", "--config", str(config)], home=home)

    assert result.returncode in (0, 1), result.stderr
    payload = json.loads(result.stdout)  # raises if stdout is not exactly JSON
    assert payload["command"]
    assert payload["bib_name"] == "ml", (
        f"{argv} reported bib_name={payload['bib_name']!r}"
    )


def test_an_unknown_flag_is_a_usage_error(library: tuple[Path, Path, Path]) -> None:
    """The class of defect this file exists for.

    A flag can be handled by a runner and never registered on the parser; every
    runner-level test passes because it builds `args` itself. Only a real
    invocation finds it.
    """
    home, config, _bib = library

    result = _run(["entries", "--no-such-flag", "--config", str(config)], home=home)

    assert result.returncode == 2
    assert "no-such-flag" in result.stderr


def test_a_broken_pipe_is_silent(library: tuple[Path, Path, Path]) -> None:
    """`pzi entries | head -1` must not print a traceback or a stderr complaint."""
    home, config, _bib = library

    piped = subprocess.run(
        f"{_CONSOLE_SCRIPT} entries --config {config} | head -1",
        shell=True, capture_output=True, text=True, timeout=60,
        env={**os.environ, "HOME": str(home), "PZI_SKIP_AUTO_START": "1"},
    )

    assert "Traceback" not in piped.stderr
    assert "BrokenPipeError" not in piped.stderr
