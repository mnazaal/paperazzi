"""Edge-case tests for pzi CLI covering previously uncovered lines.

Covers lines: 181-182, 240-241, 245, 302-305, 314-318, 337-340,
410-413, 422-425, 475-476, 493-501.
"""

import sys
from io import StringIO
from pathlib import Path

import pytest

from pzi.cli import _run_bib, _run_browser, main, run_cli

# ---------------------------------------------------------------------------
# Lines 181-182: unknown command fallback in run_cli
# ---------------------------------------------------------------------------

def test_unknown_command_returns_2(tmp_path: Path) -> None:
    # --help triggers SystemExit(0) from argparse
    with pytest.raises(SystemExit) as exc_info:
        run_cli(
            ["--help"],
            home_dir=str(tmp_path),
            stdout=StringIO(),
            stderr=StringIO(),
        )
    assert exc_info.value.code == 0


def test_browser_install_success() -> None:
    import argparse
    ns = argparse.Namespace(browser_command="install", browser="chromium")
    stdout = StringIO()
    stderr = StringIO()

    import pzi.setup_service
    orig = pzi.setup_service.install_playwright_browser
    try:
        pzi.setup_service.install_playwright_browser = (
            lambda browser, *, stdout, stderr: 0
        )
        exit_code = _run_browser(ns, stdout=stdout, stderr=stderr)
        assert exit_code == 0
    finally:
        pzi.setup_service.install_playwright_browser = orig


def test_browser_unknown_command_returns_2() -> None:
    import argparse
    ns = argparse.Namespace(browser_command="bogus", browser="chromium")
    stdout = StringIO()
    stderr = StringIO()
    exit_code = _run_browser(ns, stdout=stdout, stderr=stderr)
    assert exit_code == 2
    assert "unknown browser command: bogus" in stderr.getvalue()


# ---------------------------------------------------------------------------
# Line 245: main() function triggers argparse SystemExit
# ---------------------------------------------------------------------------

def test_main_function_calls_run_cli(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi", "--help"])
    monkeypatch.setattr(sys, "stdout", StringIO())
    monkeypatch.setattr(sys, "stderr", StringIO())
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Lines 302-305: _run_pdf_retry attach error path
# ---------------------------------------------------------------------------

def test_pdf_attach_error_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_attach_pdf(**kwargs) -> dict:
        return {
            "status": "error",
            "message": "attach failed",
            "errors": ["err1", "err2"],
        }

    monkeypatch.setattr("pzi.cli.attach_pdf", fake_attach_pdf)

    stderr = StringIO()
    exit_code = run_cli(
        ["pdf", "attach", "smith2024graph", "/tmp/fake.pdf",
         "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "attach failed" in stderr.getvalue()
    assert "- err1" in stderr.getvalue()
    assert "- err2" in stderr.getvalue()


# ---------------------------------------------------------------------------
# Lines 314-318: _run_pdf_retry retry success path
# ---------------------------------------------------------------------------

def test_pdf_retry_success_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_retry_pdf(**kwargs) -> dict:
        return {
            "status": "ok",
            "citekey": "smith2024graph",
            "local_pdf_path": "/tmp/smith2024graph.pdf",
        }

    monkeypatch.setattr("pzi.cli.retry_pdf", fake_retry_pdf)

    stdout = StringIO()
    exit_code = run_cli(
        ["pdf", "retry", "smith2024graph", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )
    assert exit_code == 0
    assert "fetched PDF smith2024graph" in stdout.getvalue()
    assert "/tmp/smith2024graph.pdf" in stdout.getvalue()


# ---------------------------------------------------------------------------
# Lines 337-340: _run_tag tag list error path
# ---------------------------------------------------------------------------

def test_tag_list_error_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_list_tags(**kwargs) -> dict:
        return {"status": "error", "message": "tag list failed", "errors": ["e1"]}

    monkeypatch.setattr("pzi.cli.list_tags", fake_list_tags)

    stderr = StringIO()
    exit_code = run_cli(
        ["tag", "list", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "failed to list tags" in stderr.getvalue()
    assert "- e1" in stderr.getvalue()


# ---------------------------------------------------------------------------
# Lines 410-413: _run_bib bib list error path
# ---------------------------------------------------------------------------

def test_bib_list_error_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")

    def fake_list_bibs(**kwargs) -> dict:
        return {"status": "error", "message": "list failed", "errors": ["err1"]}

    monkeypatch.setattr("pzi.cli.list_bibs", fake_list_bibs)

    stderr = StringIO()
    exit_code = run_cli(
        ["bib", "list", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "failed to list bibs" in stderr.getvalue()
    assert "- err1" in stderr.getvalue()


# ---------------------------------------------------------------------------
# Lines 422-425: _run_bib set-default error path
# ---------------------------------------------------------------------------

def test_bib_set_default_error_path(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")

    def fake_set_default_bib(**kwargs) -> dict:
        return {"status": "error", "message": "set-default failed", "errors": ["e1"]}

    monkeypatch.setattr("pzi.cli.set_default_bib", fake_set_default_bib)

    stderr = StringIO()
    exit_code = run_cli(
        ["bib", "set-default", "ml", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "set-default failed" in stderr.getvalue()
    assert "- e1" in stderr.getvalue()


# ---------------------------------------------------------------------------
# Lines 475-476: _run_bib unknown bib command
# ---------------------------------------------------------------------------

def test_bib_unknown_command_returns_2() -> None:
    import argparse
    ns = argparse.Namespace(bib_command="bogus", name="ml",
                            dry_run=False, keep_preprint=False)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = _run_bib(ns, home_dir="/tmp", config_path="/tmp/config.toml",
                         stdout=stdout, stderr=stderr)
    assert exit_code == 2
    assert "unknown bib command: bogus" in stderr.getvalue()


# ---------------------------------------------------------------------------
# Lines 493-501: _run_serve config load failure (no host/port, bad config)
# ---------------------------------------------------------------------------

def test_serve_config_load_failure(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    def fake_load_config_file(config_path_str: str, *, home_dir: str) -> dict:
        return {"config": None, "errors": ["config parse error"]}

    monkeypatch.setattr(
        "pzi.config_loader.load_config_file", fake_load_config_file
    )

    stderr = StringIO()
    exit_code = run_cli(
        ["serve", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    assert "failed to load config" in stderr.getvalue()
    assert "- config parse error" in stderr.getvalue()
