"""Edge tests for setup_service.py uncovered lines (145->147: subprocess failure, 148: FileNotFoundError)."""

from io import StringIO
from pathlib import Path

from pzi.setup_service import (
    install_playwright_browser,
    render_compose,
    render_config,
    run_services_command,
    service_compose_path,
    write_service_files,
)

# ── render_config ────────────────────────────────────────────────

def test_render_config_minimal() -> None:
    text = render_config(
        bib_name="main",
        bib_path="~/bibs/main.bib",
        with_browser=False,
        with_flaresolverr=False,
    )
    assert 'name = "main"' in text
    assert 'path = "~/bibs/main.bib"' in text
    assert "browser_pdf_cmd" not in text
    assert "flaresolverr_url" not in text


def test_render_config_with_browser() -> None:
    text = render_config(
        bib_name="main", bib_path="~/bibs/main.bib",
        with_browser=True, with_flaresolverr=False,
    )
    assert "browser_pdf_cmd" in text
    assert "chromium" in text


def test_render_config_with_flaresolverr() -> None:
    text = render_config(
        bib_name="main", bib_path="~/bibs/main.bib",
        with_browser=False, with_flaresolverr=True,
    )
    assert "flaresolverr_url" in text


def test_render_config_escapes_quotes() -> None:
    text = render_config(
        bib_name='test"bib',
        bib_path="/path/with\\backslash",
        with_browser=False, with_flaresolverr=False,
    )
    assert '\\"' in text  # escaped quote
    assert "\\\\" in text  # escaped backslash


# ── render_compose ───────────────────────────────────────────────

def test_render_compose_without_flaresolverr() -> None:
    text = render_compose(with_flaresolverr=False)
    assert "translation-server" in text
    assert "flaresolverr" not in text


def test_render_compose_with_flaresolverr() -> None:
    text = render_compose(with_flaresolverr=True)
    assert "translation-server" in text
    assert "flaresolverr" in text


# ── write_service_files ──────────────────────────────────────────

def test_write_service_files(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("")
    paths = write_service_files(str(config_path), with_flaresolverr=False)
    assert len(paths) == 2
    for p in paths:
        assert Path(p).exists()
    compose = config_path.parent / "compose.yml"
    assert compose.exists()


def test_write_service_files_with_flaresolverr(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("")
    write_service_files(str(config_path), with_flaresolverr=True)
    compose = config_path.parent / "compose.yml"
    assert "flaresolverr" in compose.read_text()


# ── service_compose_path ─────────────────────────────────────────

def test_service_compose_path() -> None:
    result = service_compose_path("/home/user/.pzi/config.toml")
    assert result == Path("/home/user/.pzi/compose.yml")


# ── run_services_command ─────────────────────────────────────────

def test_run_services_compose_not_found(tmp_path: Path) -> None:
    stderr = StringIO()
    rc = run_services_command(
        "up",
        config_path=str(tmp_path / "nonexistent" / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert rc == 1
    assert "service files not found" in stderr.getvalue()


def test_run_services_up(monkeypatch, tmp_path: Path) -> None:
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "Container started"
        stderr = ""

    def fake_run(args, shell=False, text=True, capture_output=True):
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text("services: {}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text("")

    stdout = StringIO()
    rc = run_services_command(
        "up", config_path=str(config_path), stdout=stdout, stderr=StringIO()
    )
    assert rc == 0
    assert "Container started" in stdout.getvalue()


def test_run_services_podman_not_found(monkeypatch, tmp_path: Path) -> None:
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text("services: {}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text("")

    stderr = StringIO()

    def fake_run(args, shell=False, text=True, capture_output=True):
        raise FileNotFoundError("podman not found")

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = run_services_command(
        "up", config_path=str(config_path), stdout=StringIO(), stderr=stderr
    )
    assert rc == 1
    assert "podman not found" in stderr.getvalue()


def test_run_services_unknown_command(tmp_path: Path, monkeypatch) -> None:
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text("services: {}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text("")

    stderr = StringIO()
    rc = run_services_command(
        "bogus", config_path=str(config_path), stdout=StringIO(), stderr=stderr
    )
    assert rc == 2
    assert "unknown services command" in stderr.getvalue()


def test_run_services_down(monkeypatch, tmp_path: Path) -> None:
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "Stopped"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text("services: {}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text("")

    stdout = StringIO()
    rc = run_services_command(
        "down", config_path=str(config_path), stdout=stdout, stderr=StringIO()
    )
    assert rc == 0


def test_run_services_status(monkeypatch, tmp_path: Path) -> None:
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "RUNNING"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    compose_path = tmp_path / "compose.yml"
    compose_path.write_text("services: {}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text("")

    stdout = StringIO()
    rc = run_services_command(
        "status", config_path=str(config_path), stdout=stdout, stderr=StringIO()
    )
    assert rc == 0


# ── install_playwright_browser ───────────────────────────────────

def test_install_playwright_success(monkeypatch) -> None:
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "Installed"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    stdout = StringIO()
    rc = install_playwright_browser("chromium", stdout=stdout, stderr=StringIO())
    assert rc == 0
    assert "Installed" in stdout.getvalue()


def test_install_playwright_failure(monkeypatch) -> None:
    import subprocess

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "Failed"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    stderr = StringIO()
    rc = install_playwright_browser("chromium", stdout=StringIO(), stderr=stderr)
    assert rc == 1
    assert "Failed" in stderr.getvalue()
