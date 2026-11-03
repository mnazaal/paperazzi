"""Non-finite batch delays are refused at the parser, before any capture."""

from __future__ import annotations

import argparse
from io import StringIO
from pathlib import Path

import pytest

from pzi.cli import run_cli
from pzi.cli_parser import _non_negative_float, build_parser


@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "Infinity", "1e999"])
@pytest.mark.parametrize("command", ["add", "inbox"])
def test_non_finite_delay_is_a_usage_error_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, value: str
) -> None:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = tmp_path / "library.bib"
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    items = tmp_path / "items.txt"
    items.write_text("10.1000/one\n10.1000/two\n")
    argv = (
        ["add", "--from-file", str(items)] if command == "add" else ["inbox", str(items)]
    )
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(
        [*argv, f"--delay={value}", "--json", "--config", str(config)],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )
    assert code == 2
    assert not bib.exists()
    assert stdout.getvalue() == ""
    assert "finite" in stderr.getvalue()


def test_finite_delays_still_parse() -> None:
    assert _non_negative_float("0") == 0.0
    assert _non_negative_float("2.5") == 2.5
    with pytest.raises(argparse.ArgumentTypeError):
        _non_negative_float("nan")


def test_every_float_option_uses_the_finite_validator() -> None:
    """No other float-typed option can reintroduce inf/nan."""
    parser = build_parser()
    stack = [parser]
    seen = 0
    while stack:
        p = stack.pop()
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                stack.extend(action.choices.values())
            elif action.type is float:
                raise AssertionError(f"bare float option {action.option_strings}")
            elif action.type is _non_negative_float:
                seen += 1
    assert seen == 2
