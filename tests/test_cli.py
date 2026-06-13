from io import StringIO
from pathlib import Path

import pytest

import pzi.cli_commands as cli_commands
from pzi.capture_models import CaptureInput, CaptureOptions, PdfCandidate
from pzi.cli import run_cli
from pzi.cli_parser import (
    build_capture_input_from_add_args,
    build_capture_options_from_add_args,
    load_add_metadata_json,
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


def test_build_capture_input_from_add_args_marks_existing_pdf_candidate_path(tmp_path: Path) -> None:
    parser = __import__("pzi.cli", fromlist=["build_parser"]).build_parser()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

    args = parser.parse_args(
        [
            "add",
            "https://example.com/paper",
            "--pdf-candidate",
            str(pdf_path),
        ]
    )

    assert build_capture_input_from_add_args(
        args, bib_selector="ml"
    ).pdf_candidates == (PdfCandidate(str(pdf_path), source="cli", kind="path"),)


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


def test_cli_init_setup_writes_config_only(tmp_path: Path) -> None:
    """`init --setup` writes config and performs NO install side effects."""
    config_path = tmp_path / "config.toml"

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
    assert stderr.getvalue() == ""
    config = config_path.read_text()
    assert '-m pzi.browser_pdf_hook --browser chromium"' in config
    assert 'path = "~/bibs/main.bib"' in config
    assert "flaresolverr_url" not in config
    assert "pzi_data_home" in config
    # config-only: guidance points at first-use bootstrap, no install ran
    out = stdout.getvalue()
    assert "playwright install" in out
    assert "pzi server" in out


def test_cli_init_setup_with_firefox(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

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
    config = config_path.read_text()
    assert '--browser firefox' in config


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


@pytest.mark.parametrize("removed", ["up", "down"])
def test_cli_services_up_down_removed(tmp_path: Path, removed: str) -> None:
    """`services up`/`down` are gone — `pzi server` owns the backend lifecycle."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []\n")

    exit_code = run_cli(
        ["services", removed, "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    # argparse rejects the unknown subcommand with exit code 2.
    assert exit_code == 2


def _write_small_library(tmp_path: Path) -> Path:
    """Write a one-entry bib + config and return the config path."""
    bib = tmp_path / "main.bib"
    bib.write_text(
        "@article{smith2024graph,\n"
        "  title = {Graph Learning},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  keywords = {ml, graphs}\n"
        "}\n"
    )
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "main"\npath = "{bib}"\ndefault = true\n')
    return config


@pytest.mark.parametrize(
    "argv",
    [
        ["list"],
        ["bib-stats"],
        ["entries"],
        ["search", "--query", "graph"],
        ["tag", "list"],
        ["dedupe"],
        ["clean"],
    ],
)
def test_cli_read_commands_emit_json(tmp_path: Path, argv: list[str]) -> None:
    """Every read/query command accepts --json and emits valid JSON to stdout."""
    import json

    config = _write_small_library(tmp_path)
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [*argv, "--json", "--config", str(config)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )
    # 0 (ok) or 1 (e.g. dedupe/clean signalling findings) — never 2 (flag rejected).
    assert exit_code in (0, 1), stderr.getvalue()
    parsed = json.loads(stdout.getvalue())  # raises if not valid JSON
    assert parsed is not None


def test_cli_uses_default_home_when_home_dir_not_injected(
    tmp_path: Path, monkeypatch
) -> None:
    from pzi.config import default_config_path

    monkeypatch.setenv("HOME", str(tmp_path))
    bib = tmp_path / "main.bib"
    bib.write_text("")
    cfg = Path(default_config_path(str(tmp_path)))
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'[[bibs]]\nname = "main"\npath = "{bib}"\ndefault = true\n')

    stdout = StringIO()
    stderr = StringIO()

    # No home_dir injected → run_cli falls back to expanduser("~") == $HOME.
    exit_code = run_cli(["list"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "main" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_pdf_attach_dispatches_to_pdf_service(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_attach_pdf(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": "ml",
            "citekey": kwargs["citekey"],
            "local_pdf_path": str(tmp_path / "paper.pdf"),
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(cli_commands, "attach_pdf", fake_attach_pdf)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        [
            "pdf",
            "attach",
            "smith2024graph",
            "https://example.com/paper.pdf",
            "--config",
            str(tmp_path / "config.toml"),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": None,
            "citekey": "smith2024graph",
            "source": "https://example.com/paper.pdf",
        }
    ]
    assert "attached" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_pdf_retry_dispatches_to_pdf_service(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_retry_pdf(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": "ml",
            "citekey": kwargs["citekey"],
            "local_pdf_path": str(tmp_path / "paper.pdf"),
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(cli_commands, "retry_pdf", fake_retry_pdf)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        [
            "pdf",
            "retry",
            "smith2024graph",
            "--config",
            str(tmp_path / "config.toml"),
        ],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": None,
            "citekey": "smith2024graph",
        }
    ]
    assert "fetched" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_promote_dispatches_to_promote_service(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_promote_bib(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": "ml",
            "dry_run": kwargs["dry_run"],
            "items": [],
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(cli_commands, "promote_bib", fake_promote_bib)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        ["promote", "--replace", "--config", str(tmp_path / "config.toml")],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": None,
            "dry_run": False,
            "keep_preprint": False,
        }
    ]
    assert stderr.getvalue() == ""


def test_cli_browser_command_removed(tmp_path: Path) -> None:
    """`pzi browser install` is gone — use `playwright install` directly."""
    exit_code = run_cli(
        ["browser", "install", "firefox"],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert exit_code == 2  # argparse rejects the unknown command


def test_cli_watch_command_removed(tmp_path: Path) -> None:
    """`pzi watch` is gone — use a file watcher like `entr` piped to `pzi import`."""
    exit_code = run_cli(
        ["watch", str(tmp_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )
    assert exit_code == 2  # argparse rejects the unknown command
