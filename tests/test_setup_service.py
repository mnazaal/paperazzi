"""Edge-case tests covering uncovered lines in src/pzi/setup_service.py.

Lines covered: 28->30, 120, 122, 126-127, 131-133, 134->136, 137, 143-149.
"""

import io
from unittest.mock import MagicMock, patch

from pzi.setup_service import (
    install_playwright_browser,
    render_config,
    run_services_command,
)

# ── lines 28-30: render_config with_browser / with_flaresolverr ─────────────

def test_render_config_with_browser_adds_browser_line() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=True,
        with_flaresolverr=False,
    )
    assert 'browser_pdf_cmd = "pzi-browser-hook --browser chromium"' in result


def test_render_config_with_flaresolverr_adds_flaresolverr_line() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=False,
        with_flaresolverr=True,
    )
    assert 'flaresolverr_url = "http://127.0.0.1:8191"' in result


# ── line 120: action "up" appends ["up", "-d"] ──────────────────────────────

def test_run_services_command_up_action() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch("pzi.setup_service.service_compose_path") as mock_path_func:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path_func.return_value = mock_path

        with patch("pzi.setup_service.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.stderr = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            rc = run_services_command(
                "up", config_path="/tmp/config.toml",
                stdout=stdout, stderr=stderr,
            )

    assert rc == 0
    called_args = mock_run.call_args[0][0]
    assert called_args[-2:] == ["up", "-d"]


# ── line 122: action "down" appends "down" ──────────────────────────────────

def test_run_services_command_down_action() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch("pzi.setup_service.service_compose_path") as mock_path_func:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path_func.return_value = mock_path

        with patch("pzi.setup_service.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_result.stderr = ""
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            rc = run_services_command(
                "down", config_path="/tmp/config.toml",
                stdout=stdout, stderr=stderr,
            )

    assert rc == 0
    called_args = mock_run.call_args[0][0]
    assert "down" in called_args


# ── lines 126-127: unknown action prints error + returns 2 ──────────────────

def test_run_services_command_unknown_action() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch("pzi.setup_service.service_compose_path") as mock_path_func:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path_func.return_value = mock_path

        rc = run_services_command(
            "bogus", config_path="/tmp/config.toml",
            stdout=stdout, stderr=stderr,
        )

    assert rc == 2
    assert "unknown services command" in stderr.getvalue()


# ── lines 131-133: podman not found (FileNotFoundError) ─────────────────────

def test_run_services_command_podman_not_found() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch("pzi.setup_service.service_compose_path") as mock_path_func:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path_func.return_value = mock_path

        with patch("pzi.setup_service.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError

            rc = run_services_command(
                "up", config_path="/tmp/config.toml",
                stdout=stdout, stderr=stderr,
            )

    assert rc == 1
    assert "podman not found" in stderr.getvalue()


# ── lines 134-137: result stdout/stderr forwarded to caller streams ─────────

def test_run_services_command_forwards_stdout_and_stderr() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch("pzi.setup_service.service_compose_path") as mock_path_func:
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path_func.return_value = mock_path

        with patch("pzi.setup_service.subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = "out-line\n"
            mock_result.stderr = "err-line\n"
            mock_result.returncode = 0
            mock_run.return_value = mock_result

            rc = run_services_command(
                "status", config_path="/tmp/config.toml",
                stdout=stdout, stderr=stderr,
            )

    assert rc == 0
    assert stdout.getvalue() == "out-line\n"
    assert stderr.getvalue() == "err-line\n"


# ── lines 143-149: install_playwright_browser ───────────────────────────────

def test_install_playwright_browser_runs_correct_command() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with patch("pzi.setup_service.subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.stdout = "download complete\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        rc = install_playwright_browser("chromium", stdout=stdout, stderr=stderr)

    assert rc == 0
    args = mock_run.call_args[0][0]
    assert "playwright" in args
    assert "chromium" in args
    assert stdout.getvalue() == "download complete\n"
