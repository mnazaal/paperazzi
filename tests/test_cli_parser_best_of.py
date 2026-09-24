"""`update --best-of` is `_positive_int` at the parser — item 631.

Mirrors `test_update_promote_limit_below_one_is_a_parser_usage_error` in
`tests/test_cli.py`, which pinned the same migration for `--limit`: this used
to be a runner-side check in `commands/update.py` that produced the same exit
code but, under `--json`, no envelope at all — the parser rejection is the one
path that never emits one, even with `--json` passed. Kept in its own file
(not `tests/test_cli.py`, which every lane of the 2026-09-24 audit reads but
none owns) so this lane's change doesn't collide with the others editing the
same shared file.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from pzi.cli import run_cli


def _batch_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path / "library.bib"}"\ndefault = true\n'
    )
    return config_path


@pytest.mark.parametrize("value", ["0", "-3"])
def test_update_promote_best_of_below_one_is_a_parser_usage_error(
    tmp_path: Path, value: str
) -> None:
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["update", "--promote", "--best-of", value, "--json",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )
    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "must be one or greater" in stderr.getvalue()


def test_update_promote_best_of_accepts_a_positive_value(tmp_path: Path) -> None:
    """A red-herring guard against over-fixing: a valid --best-of must still work."""
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["update", "--promote", "--best-of", "2", "--limit", "1",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )
    # Nothing in the library to promote, so this is a clean, empty run — the
    # point is that argument parsing itself did not reject a valid value.
    assert exit_code in (0, 1)
    assert "must be one or greater" not in stderr.getvalue()
