"""An interrupted batch's retry file lists every input still outstanding."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pzi.cli import run_cli
from pzi.commands import add as add_command

_VALUES = ["10.1000/one", "10.1000/two", "10.1000/three"]


def _ok(value: str) -> dict[str, Any]:
    return {
        "status": "ok", "action": "insert", "citekey": "k", "message": "captured",
        "warnings": [], "errors": [],
    }


@pytest.mark.parametrize("during", ["capture", "delay"])
def test_interrupted_batch_retry_file_keeps_the_unprocessed_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during: str
) -> None:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = tmp_path / "library.bib"
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    source = tmp_path / "queue.txt"
    source.write_text("\n".join(_VALUES) + "\n")
    retry = tmp_path / "resume.txt"
    attempted: list[str] = []

    def capture(captured_input: Any, *_a: Any, **_k: Any) -> dict[str, Any]:
        attempted.append(captured_input.value)
        if during == "capture" and len(attempted) == 2:
            raise KeyboardInterrupt
        return _ok(captured_input.value)

    def interrupt_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(add_command, "capture_to_bib", capture)
    if during == "delay":
        monkeypatch.setattr(add_command.time, "sleep", interrupt_sleep)
    stdout, stderr = StringIO(), StringIO()

    code = run_cli(
        ["add", "--from-file", str(source), "--failures-out", str(retry), "--json",
         "--delay", "1" if during == "delay" else "0", "--config", str(config)],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )

    assert code == 130
    # Item one was captured; resuming must not repeat it.
    assert retry.read_text().splitlines() == _VALUES[1:]
    assert attempted == (_VALUES[:2] if during == "capture" else _VALUES[:1])
    assert source.read_text().splitlines() == _VALUES
    assert json.loads(stdout.getvalue())["counts"]["added"] == 1


@pytest.mark.parametrize("during", ["capture", "delay"])
@pytest.mark.parametrize("as_json", [False, True])
def test_interrupted_batch_report_says_so_and_counts_what_the_retry_file_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during: str, as_json: bool
) -> None:
    """Text and JSON both report the interrupt and the outstanding count, and
    "wrote N" equals the retry file's line count rather than the failure count."""
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = tmp_path / "library.bib"
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    values = [f"10.1000/v{i}" for i in range(5)]
    source = tmp_path / "queue.txt"
    source.write_text("\n".join(values) + "\n")
    retry = tmp_path / "resume.txt"
    attempted: list[str] = []

    def capture(captured_input: Any, *_a: Any, **_k: Any) -> dict[str, Any]:
        attempted.append(captured_input.value)
        if during == "capture" and len(attempted) == 2:
            raise KeyboardInterrupt
        return _ok(captured_input.value)

    def interrupt_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(add_command, "capture_to_bib", capture)
    if during == "delay":
        monkeypatch.setattr(add_command.time, "sleep", interrupt_sleep)
    stdout, stderr = StringIO(), StringIO()
    argv = ["add", "--from-file", str(source), "--failures-out", str(retry),
            "--delay", "1" if during == "delay" else "0", "--config", str(config)]

    code = run_cli(
        argv + (["--json"] if as_json else []),
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )

    assert code == 130
    lines = retry.read_text().splitlines()
    assert lines == values[1:]
    outstanding = len(lines)
    if as_json:
        errors = json.loads(stdout.getvalue())["errors"]
        assert f"interrupted: {outstanding} input(s) outstanding" in errors
    else:
        out = stdout.getvalue()
        assert f"interrupted: {outstanding} input(s) outstanding" in out
        assert f"wrote {len(lines)} " in out


@pytest.mark.parametrize(
    ("during", "at", "fail"),
    [
        ("capture", 0, None), ("capture", 2, None), ("capture", 4, None),
        ("delay", 1, None), ("delay", 4, None),
        ("capture", 3, 1), ("delay", 3, 1),
    ],
)
@pytest.mark.parametrize("as_json", [False, True])
def test_interrupt_at_any_position_accounts_for_every_input_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during: str, at: int,
    fail: int | None, as_json: bool,
) -> None:
    """added + exists + failed + outstanding == total, with outstanding derived
    from where the interrupt landed (not from the retry file), and an interrupted
    batch reports `error` like its exit code 130 says."""
    import re

    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = tmp_path / "library.bib"
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    values = [f"10.1000/v{i}" for i in range(5)]
    source = tmp_path / "queue.txt"
    source.write_text("\n".join(values) + "\n")
    retry = tmp_path / "resume.txt"
    sleeps = {"n": 0}

    def capture(captured_input: Any, *_a: Any, **_k: Any) -> dict[str, Any]:
        index = values.index(captured_input.value)
        if during == "capture" and index == at:
            raise KeyboardInterrupt
        if index == fail:
            return {"status": "error", "citekey": None, "message": "boom",
                    "errors": ["boom"], "warnings": []}
        return _ok(captured_input.value)

    def sleep(_seconds: float) -> None:
        sleeps["n"] += 1  # this sleep precedes input number sleeps["n"]
        if during == "delay" and sleeps["n"] == at:
            raise KeyboardInterrupt

    monkeypatch.setattr(add_command, "capture_to_bib", capture)
    monkeypatch.setattr(add_command.time, "sleep", sleep)
    stdout, stderr = StringIO(), StringIO()

    code = run_cli(
        ["add", "--from-file", str(source), "--failures-out", str(retry),
         "--delay", "1", "--config", str(config), *(["--json"] if as_json else [])],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )

    expected_outstanding = len(values) - at
    expected_failed = 1 if fail is not None else 0
    expected_added = at - expected_failed
    assert code == 130
    if as_json:
        doc = json.loads(stdout.getvalue())
        assert doc["counts"] == {"added": expected_added, "exists": 0, "failed": expected_failed}
        assert f"interrupted: {expected_outstanding} input(s) outstanding" in doc["errors"]
        assert doc["status"] == "error"
    else:
        out = stdout.getvalue()
        match = re.search(r"done: (\d+) added, (\d+) already present, (\d+) failed", out)
        assert match is not None, out
        assert [int(g) for g in match.groups()] == [expected_added, 0, expected_failed]
        assert f"interrupted: {expected_outstanding} input(s) outstanding" in out
        assert f"wrote {expected_failed + expected_outstanding} " in out
    assert len(retry.read_text().splitlines()) == expected_failed + expected_outstanding
