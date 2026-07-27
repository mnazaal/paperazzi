"""Exit-code contract for `pzi fix dedupe`."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pzi.commands import dedupe as dedupe_command


def _result(*, exact: int = 0, fuzzy: int = 0) -> dict[str, Any]:
    return {
        "status": "ok",
        "bib_path": "/tmp/lib.bib",
        "total_entries": 2,
        "exact_duplicates": [{"citekeys": ["a", "b"]}] * exact,
        "fuzzy_candidates": [{"citekey": "a", "hint": "b"}] * fuzzy,
        "total_clusters": exact,
        "errors": [],
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: dict[str, Any],
    *,
    json_output: bool = False,
) -> tuple[int, str]:
    monkeypatch.setattr(
        dedupe_command, "resolve_target", lambda **_kw: ({}, {"path": "/tmp/lib.bib"})
    )
    monkeypatch.setattr(dedupe_command, "find_duplicates", lambda **_kw: result)
    stdout, stderr = StringIO(), StringIO()
    code = dedupe_command.run_dedupe_command(
        Namespace(config=None, target=None, json=json_output),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
    )
    return code, stdout.getvalue()


def test_dedupe_exits_zero_when_nothing_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _out = _run(monkeypatch, tmp_path, _result())
    assert code == 0


def test_dedupe_exits_one_on_exact_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _out = _run(monkeypatch, tmp_path, _result(exact=1))
    assert code == 1


def test_dedupe_exits_one_on_fuzzy_candidates_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 1 means "ran fine, has something to report".

    ``total_clusters`` counts only exact clusters, so keying the exit code off
    it alone reported success on a library whose sole finding was fuzzy.
    """
    code, out = _run(monkeypatch, tmp_path, _result(fuzzy=1))
    assert code == 1
    assert "fuzzy" in out


def test_dedupe_exits_one_on_fuzzy_candidates_alone_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _out = _run(monkeypatch, tmp_path, _result(fuzzy=1), json_output=True)
    assert code == 1
