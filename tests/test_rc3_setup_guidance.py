"""`pzi init --setup` points at the install that exists (GitHub, not PyPI)."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from pzi.cli import run_cli


def test_setup_guidance_installs_from_github_not_pypi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    stdout = StringIO()
    code = run_cli(
        ["init", "--setup", "--bib", str(tmp_path / "lib.bib"),
         "--config", str(tmp_path / "config.toml")],
        home_dir=str(tmp_path), stdout=stdout, stderr=StringIO(),
    )
    assert code == 0
    out = stdout.getvalue()
    assert "git+https://github.com/mnazaal/paperazzi.git" in out
    assert "pzi[playwright] @" in out
    assert "pip install" not in out
    assert "pipx install 'pzi[playwright]'" not in out
