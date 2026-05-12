from io import StringIO
from pathlib import Path

from pzi import setup_service
from pzi.cli import run_cli


def _fake_fetch_web(url: str, *, server_url: str) -> list[dict]:
    return [
        {
            "item_type": "webpage",
            "record": {
                "source_url": url,
                "canonical_url": url,
                "abstract_url": url,
            },
            "attachments": [],
        }
    ]


def _fake_fetch_search(query: str, *, server_url: str) -> list[dict]:
    return [{"item_type": "journalArticle", "record": {}, "attachments": []}]


def test_cli_add_inserts_entry_and_prints_success(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "10.1234/foo",
            "--citekey",
            "smith2024graph",
            "--title",
            "Graph Parsers",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
        fetch_search=_fake_fetch_search,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "insert smith2024graph in ml\n"
    assert stderr.getvalue() == ""
    assert "doi = {10.1234/foo}" in bib_path.read_text()


def test_cli_add_supports_dry_run(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "https://example.com/paper",
            "--title",
            "Graph Parsers",
            "--dry-run",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
        fetch_web=_fake_fetch_web,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "DRY RUN: insert unknownxxxxgraph in ml\n"
    assert stderr.getvalue() == ""
    assert not bib_path.exists()


def test_cli_add_renders_service_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "10.1234/foo",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert (
        stderr.getvalue() == "failed to load config\n- bibs must be a non-empty list\n"
    )


def test_cli_tag_add_renders_service_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "tag",
            "add",
            "smith2024graph",
            "ml",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue().startswith(
        "could not resolve target bib\n- config file not found:"
    )


def test_cli_add_parses_authors_and_tags(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "https://example.com/paper.pdf",
            "--citekey",
            "smith2024graph",
            "--title",
            "Graph Parsers",
            "--authors",
            "Smith, Jane;Doe, John",
            "--tags",
            "graphs, ML",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
        fetch_web=_fake_fetch_web,
    )

    assert exit_code == 0
    contents = bib_path.read_text()
    assert "author = {Smith, Jane and Doe, John}" in contents
    assert "keywords = {graphs, ml}" in contents
    assert "PDF: https://example.com/paper.pdf" in contents


def test_cli_add_generates_citekey_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "https://example.com/paper",
            "--title",
            "Graph Parsers",
            "--authors",
            "Smith, Jane",
            "--year",
            "2024",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
        fetch_web=_fake_fetch_web,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "insert smith2024graph in ml\n"
    assert stderr.getvalue() == ""
    assert "@article{smith2024graph," in bib_path.read_text()


def test_cli_init_setup_writes_config_services_and_installs_browser(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    calls: list[str] = []

    def fake_install(browser: str, *, stdout, stderr) -> int:
        calls.append(browser)
        print(f"installed {browser}", file=stdout)
        return 0

    monkeypatch.setattr(setup_service, "install_playwright_browser", fake_install)

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "init",
            "--setup",
            "--bib",
            "~/bibs/main.bib",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == ["chromium"]
    assert stderr.getvalue() == ""
    config = config_path.read_text()
    assert 'browser_pdf_cmd = "pzi-browser-hook --browser chromium"' in config
    assert 'path = "~/bibs/main.bib"' in config
    assert "flaresolverr_url" not in config
    compose = tmp_path / "compose.yml"
    containerfile = tmp_path / "containers" / "translation-server" / "Containerfile"
    assert compose.exists()
    assert containerfile.exists()
    assert "translation-server:" in compose.read_text()
    assert "flaresolverr:" not in compose.read_text()
    assert "installed chromium" in stdout.getvalue()


def test_cli_init_setup_can_include_flaresolverr(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(
        setup_service,
        "install_playwright_browser",
        lambda browser, *, stdout, stderr: 0,
    )

    exit_code = run_cli(
        [
            "init",
            "--setup",
            "--with-flaresolverr",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert 'flaresolverr_url = "http://127.0.0.1:8191"' in config_path.read_text()
    assert "flaresolverr:" in (tmp_path / "compose.yml").read_text()


def test_cli_services_status_runs_managed_compose(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    (tmp_path / "compose.yml").write_text("services: {}\n")
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, *, shell, text, capture_output):
        calls.append(args)
        return Result()

    monkeypatch.setattr(setup_service.subprocess, "run", fake_run)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["services", "status", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "ok\n"
    assert stderr.getvalue() == ""
    assert calls == [["podman", "compose", "-f", str(tmp_path / "compose.yml"), "ps"]]


def test_cli_services_reports_missing_managed_files(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["services", "up", "--config", str(tmp_path / "config.toml")],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "run: pzi init --setup" in stderr.getvalue()


def test_cli_browser_install_delegates_to_playwright(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_install(browser: str, *, stdout, stderr) -> int:
        calls.append(browser)
        print("done", file=stdout)
        return 0

    monkeypatch.setattr(setup_service, "install_playwright_browser", fake_install)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["browser", "install", "firefox"],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == ["firefox"]
    assert stdout.getvalue() == "done\n"
    assert stderr.getvalue() == ""
