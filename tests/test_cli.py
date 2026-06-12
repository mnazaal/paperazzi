from io import StringIO
from pathlib import Path

from pzi import setup_service
from pzi.capture_models import CaptureInput, CaptureOptions, PdfCandidate
from pzi.cli import (
    build_capture_input_from_add_args,
    build_capture_options_from_add_args,
    load_add_metadata_json,
    run_cli,
)


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


def test_load_add_metadata_json_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    path.write_text('{"title": "Graph Parsers", "year": 2024}')

    assert load_add_metadata_json(str(path), stdin_text=None) == {
        "title": "Graph Parsers",
        "year": 2024,
    }


def test_load_add_metadata_json_reads_stdin_marker() -> None:
    assert load_add_metadata_json("-", stdin_text='{"title": "From stdin"}') == {
        "title": "From stdin"
    }


def test_build_capture_input_from_add_args_keeps_cli_capture_hints() -> None:
    parser = __import__("pzi.cli", fromlist=["build_parser"]).build_parser()
    args = parser.parse_args(
        [
            "add",
            "https://example.com/paper",
            "--title",
            "Graph Parsers",
            "--pdf-candidate",
            "https://example.com/a.pdf",
            "--pdf-candidate",
            "https://example.com/b.pdf",
        ]
    )

    assert build_capture_input_from_add_args(args, bib_selector="ml") == CaptureInput(
        value="https://example.com/paper",
        record_overrides={"title": "Graph Parsers"},
        bib_selector="ml",
        pdf_candidates=(
            PdfCandidate("https://example.com/a.pdf", source="cli"),
            PdfCandidate("https://example.com/b.pdf", source="cli"),
        ),
    )


def test_build_capture_options_from_add_args_prefers_cli_page_metadata_cmd() -> None:
    parser = __import__("pzi.cli", fromlist=["build_parser"]).build_parser()
    args = parser.parse_args(
        [
            "add",
            "https://example.com/paper",
            "--page-metadata-cmd",
            "cli-tool --json",
            "--dry-run",
        ]
    )

    assert build_capture_options_from_add_args(
        args,
        config={
            "page_metadata_cmd": "config-tool",
            "page_metadata_timeout_seconds": 11,
        },
    ) == CaptureOptions(
        dry_run=True,
        force_new=False,
        page_metadata_cmd="cli-tool --json",
        page_metadata_timeout_seconds=11,
    )


def test_build_capture_input_from_add_args_loads_cookie_file(tmp_path: Path) -> None:
    parser = __import__("pzi.cli", fromlist=["build_parser"]).build_parser()
    cookie_path = tmp_path / "cookies.txt"
    cookie_path.write_text("sid=abc123\n")

    args = parser.parse_args(
        [
            "add",
            "https://example.com/paper",
            "--cookie-file",
            str(cookie_path),
        ]
    )

    assert build_capture_input_from_add_args(
        args, bib_selector="ml"
    ).auth_hints.cookies == "sid=abc123"


def test_build_capture_input_from_add_args_loads_page_html(tmp_path: Path) -> None:
    parser = __import__("pzi.cli", fromlist=["build_parser"]).build_parser()
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><title>Test</title></html>")

    args = parser.parse_args(
        [
            "add",
            "https://example.com/paper",
            "--page-html",
            str(html_path),
        ]
    )

    capture = build_capture_input_from_add_args(args, bib_selector="ml")
    assert capture.page_artifact is not None
    assert capture.page_artifact.html == "<html><title>Test</title></html>"
    assert capture.page_artifact.source == "file"


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


def test_cli_add_outputs_json_when_requested(tmp_path: Path) -> None:
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
            "--title",
            "Graph Parsers",
            "--json",
            "--config",
            str(config_path),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
        fetch_search=_fake_fetch_search,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    payload = __import__("json").loads(stdout.getvalue())
    assert payload["status"] == "ok"
    assert payload["bib_name"] == "ml"
    assert payload["citekey"] == "unknownxxxxgraph"


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
    output = stdout.getvalue()
    assert "DRY RUN: insert unknownxxxxgraph in ml" in output
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


def test_cli_init_setup_writes_config_and_installs_browser(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    calls: list[str] = []

    def fake_install(browser: str, *, stdout, stderr) -> int:
        calls.append(browser)
        print(f"installed {browser}", file=stdout)
        return 0

    monkeypatch.setattr(setup_service, "install_playwright_browser", fake_install)

    # Patch ts_backend functions to avoid real network/subprocess calls
    import pzi.ts_backend as tsb

    def fake_ensure_node(data_home, *, interactive, stdout, stderr):
        return "/usr/bin/node"

    def fake_ensure_translation_server(data_home, node, *, stdout, stderr):
        return data_home / "ts"

    monkeypatch.setattr(tsb, "ensure_node", fake_ensure_node)
    monkeypatch.setattr(tsb, "ensure_translation_server", fake_ensure_translation_server)

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
    assert '-m pzi.browser_pdf_hook --browser chromium"' in config
    assert 'path = "~/bibs/main.bib"' in config
    assert "flaresolverr_url" not in config
    assert "pzi_data_home" in config
    assert "installed chromium" in stdout.getvalue()


def test_cli_init_setup_with_firefox(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    calls: list[str] = []

    def fake_install(browser: str, *, stdout, stderr) -> int:
        calls.append(browser)
        print(f"installed {browser}", file=stdout)
        return 0

    monkeypatch.setattr(setup_service, "install_playwright_browser", fake_install)

    import pzi.ts_backend as tsb

    def fake_ensure_node(data_home, *, interactive, stdout, stderr):
        return "/usr/bin/node"

    def fake_ensure_translation_server(data_home, node, *, stdout, stderr):
        return data_home / "ts"

    monkeypatch.setattr(tsb, "ensure_node", fake_ensure_node)
    monkeypatch.setattr(tsb, "ensure_translation_server", fake_ensure_translation_server)

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "init",
            "--setup",
            "--browser",
            "firefox",
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
    assert calls == ["firefox"]
    config = config_path.read_text()
    assert '--browser firefox' in config
    assert "installed firefox" in stdout.getvalue()


def test_cli_services_handles_missing_config(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()

    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []\n")

    exit_code = run_cli(
        ["services", "status", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert "failed to load config" in stderr.getvalue()


def test_cli_services_up_needs_config(tmp_path: Path, monkeypatch) -> None:
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    stdout = StringIO()
    stderr = StringIO()

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
translation_server_url = "http://127.0.0.1:19999"
""".strip()
    )

    # Mock auto_start_ts to return False without real I/O
    import pzi.ts_backend as tsb
    monkeypatch.setattr(tsb, "is_ts_reachable", lambda url, **kw: False)
    monkeypatch.setattr(tsb, "ensure_node", lambda *a, **kw: None)

    exit_code = run_cli(
        ["services", "up", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1


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
