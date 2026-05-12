"""Edge tests for cli.py uncovered lines (181-182, 240-241, 475-476, 499-501).

Also covers 493-498 (serve config missing host/port path).
"""

from io import StringIO
from pathlib import Path

import pytest

from pzi.cli import run_cli

# ── Lines 181-182 / 240-241 / 475-476: argparse exits before run_cli ──

def test_unknown_command_argparse_exits(tmp_path: Path) -> None:
    """Argparse rejects unknown command with SystemExit(2)."""
    stderr = StringIO()
    with pytest.raises(SystemExit) as exc_info:
        run_cli(
            ["bogus_command"],
            home_dir=str(tmp_path),
            stdout=StringIO(),
            stderr=stderr,
        )
    assert exc_info.value.code == 2


def test_browser_unknown_subcommand_argparse_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_cli(
            ["browser", "bogus"],
            home_dir=str(tmp_path),
            stdout=StringIO(),
            stderr=StringIO(),
        )
    assert exc_info.value.code == 2


def test_bib_unknown_subcommand_argparse_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run_cli(
            ["bib", "bogus", "--config", str(tmp_path / "config.toml")],
            home_dir=str(tmp_path),
            stdout=StringIO(),
            stderr=StringIO(),
        )
    assert exc_info.value.code == 2


# ── Lines 181-182: unknown command fallback after argparse (hypothetical) ───

def test_run_cli_unknown_command_fallback(monkeypatch, tmp_path: Path) -> None:
    """Cover the 'unknown command' print at end of run_cli using a fake parser."""
    import argparse

    class FakeNamespace:
        command = "bogus"
        config = None

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self, args: FakeNamespace())

    stderr = StringIO()
    rc = run_cli(
        ["anything"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert rc == 2
    assert "unknown command: bogus" in stderr.getvalue()


def test_browser_unknown_command_fallback(monkeypatch, tmp_path: Path) -> None:
    """Cover _run_browser 'unknown browser command' branch."""
    import argparse

    class FakeNamespace:
        command = "browser"
        browser_command = "bogus"
        config = None

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self, args: FakeNamespace())

    stderr = StringIO()
    rc = run_cli(
        ["browser", "bogus"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert rc == 2
    assert "unknown browser command: bogus" in stderr.getvalue()


def test_bib_unknown_command_fallback(monkeypatch, tmp_path: Path) -> None:
    """Cover _run_bib 'unknown bib command' branch."""
    import argparse

    class FakeNamespace:
        command = "bib"
        bib_command = "bogus"
        config = None

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self, args: FakeNamespace())

    stderr = StringIO()
    rc = run_cli(
        ["bib", "bogus"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert rc == 2
    assert "unknown bib command: bogus" in stderr.getvalue()


# ── Lines 493-501: serve config load failure ─────────────────────

def test_serve_config_load_failure(tmp_path: Path, monkeypatch) -> None:
    """When load_config_file returns config=None, _run_serve prints error and returns 1."""
    config_path = tmp_path / "config.toml"

    def fake_load(path: str, *, home_dir: str) -> dict:
        return {"config": None, "errors": ["bad toml"]}

    monkeypatch.setattr("pzi.config_loader.load_config_file", fake_load)

    stderr = StringIO()
    rc = run_cli(
        ["serve", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert rc == 1
    assert "failed to load config" in stderr.getvalue()
    assert "- bad toml" in stderr.getvalue()


# ── Line 499-501: serve with host/port provided directly ─────────

def test_serve_with_explicit_host_port(monkeypatch) -> None:
    """When --host and --port are both provided, config is not needed."""
    captured = {}

    def fake_run_server(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("pzi.http_api.run_server", fake_run_server)

    stdout = StringIO()
    rc = run_cli(
        ["serve", "--host", "0.0.0.0", "--port", "9999"],
        home_dir="/tmp",
        stdout=stdout,
        stderr=StringIO(),
    )
    assert rc == 0
    assert "serving on 0.0.0.0:9999" in stdout.getvalue()


# ── Additional edge: init --force overwrites ─────────────────────

def test_init_force_overwrites_existing(tmp_path: Path) -> None:
    """--force overwrites an existing config."""
    config = tmp_path / "config.toml"
    config.write_text("existing content")

    stdout = StringIO()
    rc = run_cli(
        ["init", "--config", str(config), "--force"],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert rc == 0
    assert "created" in stdout.getvalue()


def test_init_config_already_exists_no_force(tmp_path: Path) -> None:
    """Without --force, existing config is not overwritten."""
    config = tmp_path / "config.toml"
    config.write_text("existing")

    stderr = StringIO()
    rc = run_cli(
        ["init", "--config", str(config)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert rc == 1
    assert "already exists" in stderr.getvalue()


def test_init_setup_mode(tmp_path: Path, monkeypatch) -> None:
    """--setup writes config, service files, installs browser."""
    config = tmp_path / "config.toml"
    monkeypatch.setattr(
        "pzi.setup_service.install_playwright_browser",
        lambda browser, stdout, stderr: 0,
    )

    stdout = StringIO()
    rc = run_cli(
        ["init", "--config", str(config), "--setup", "--bib", str(tmp_path / "bib.bib")],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert rc == 0
    assert "created" in stdout.getvalue()
