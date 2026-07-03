import importlib
from argparse import ArgumentParser, Namespace, _SubParsersAction
from io import StringIO
from pathlib import Path

import pytest

import pzi.cli as cli
import pzi.commands.export as export_command
from pzi.capture_models import CaptureInput, CaptureOptions, PdfCandidate
from pzi.cli import run_cli
from pzi.cli_parser import (
    build_capture_input_from_add_args,
    build_capture_options_from_add_args,
    build_parser,
    load_add_metadata_json,
    parse_batch_values,
)
from pzi.commands.pdf import run_pdf_command
from pzi.commands.update import run_update_command


def _parser_command_names(parser: ArgumentParser) -> set[str]:
    for action in parser._actions:
        if isinstance(action, _SubParsersAction):
            return set(action.choices)
    raise AssertionError("parser has no subcommands")


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


def test_cli_dispatch_registry_covers_all_parser_commands() -> None:
    parser_commands = _parser_command_names(build_parser())

    assert parser_commands <= set(cli.CLI_COMMANDS)
    assert "fix" in cli.CLI_COMMANDS
    # Removed top-level commands must not linger in the dispatch registry.
    removed = {"list", "set-default", "version", "clean", "dedupe", "merge",
               "reindex", "services"}
    assert not (removed & set(cli.CLI_COMMANDS))


def test_pdf_runner_name_matches_command_scope() -> None:
    assert hasattr(cli, "_run_pdf")
    assert not hasattr(cli, "_run_pdf_retry")


def test_export_runner_lives_in_command_module() -> None:
    assert cli._run_export is export_command.run_export_command


def test_import_runner_lives_in_command_module() -> None:
    import_command = importlib.import_module("pzi.commands.import_")

    assert cli._run_import is import_command.run_import_command


def test_fix_runner_lives_in_command_module() -> None:
    fix_command = importlib.import_module("pzi.commands.fix")
    assert cli._run_fix is fix_command.run_fix_command


def test_fix_dispatches_to_maintenance_runners() -> None:
    fix_command = importlib.import_module("pzi.commands.fix")
    clean_command = importlib.import_module("pzi.commands.clean")
    dedupe_command = importlib.import_module("pzi.commands.dedupe")
    reindex_command = importlib.import_module("pzi.commands.reindex")
    assert fix_command._SUBCOMMANDS == {
        "clean": clean_command.run_clean_command,
        "dedupe": dedupe_command.run_dedupe_command,
        "merge": dedupe_command.run_merge_command,
        "reindex": reindex_command.run_reindex_command,
    }


def test_bib_entry_runners_live_in_command_modules() -> None:
    delete_command = importlib.import_module("pzi.commands.delete")
    entries_command = importlib.import_module("pzi.commands.entries")

    assert cli._run_delete is delete_command.run_delete_command
    assert cli._run_entries is entries_command.run_entries_command


def test_setup_runner_lives_in_command_module() -> None:
    init_command = importlib.import_module("pzi.commands.init")

    assert cli._run_init is init_command.run_init_command


def test_add_health_server_runners_live_in_command_modules() -> None:
    add_command = importlib.import_module("pzi.commands.add")
    doctor_command = importlib.import_module("pzi.commands.doctor")
    server_command = importlib.import_module("pzi.commands.server")

    assert cli._run_add is add_command.run_add_command
    assert cli._run_doctor is doctor_command.run_doctor_command
    assert cli._run_server is server_command.run_server_command


def test_top_level_help_is_grouped_with_examples() -> None:
    stdout = StringIO()

    exit_code = run_cli([], stdout=stdout, stderr=StringIO())

    assert exit_code == 0
    help_text = stdout.getvalue()
    # Examples lead the listing, commands are grouped, and every command appears.
    assert "EXAMPLES" in help_text
    assert "CAPTURE" in help_text and "MAINTAIN" in help_text
    for command in ("add", "search", "fix", "doctor"):
        assert f"\n  {command} " in help_text or f"\n  {command}\n" in help_text


def test_top_level_help_is_plain_text() -> None:
    # Help output carries no ANSI escape sequences (plain text everywhere).
    stdout = StringIO()
    run_cli([], stdout=stdout, stderr=StringIO())
    assert "\x1b[" not in stdout.getvalue()


def test_init_setup_help_says_configure_not_install(capsys) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["init", "--help"])

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "configure translation-server" in help_text
    assert "install translation-server" not in help_text


def test_cli_detail_renders_author_names(tmp_path: Path) -> None:
    # Regression: detail rendering assumed CSL given/family dicts and printed an
    # empty "authors:" line for the plain "Family, Given" strings the service emits.
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    bib_path.write_text(
        "@article{smith2020graph,\n"
        "  title = {Graph Neural Networks},\n"
        "  author = {Smith, John and Doe, Jane},\n"
        "  year = {2020},\n"
        "}\n"
    )
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    stdout = StringIO()

    exit_code = run_cli(
        ["entries", "smith2020graph", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "authors: Smith, John; Doe, Jane" in stdout.getvalue()


def test_detail_author_name_handles_strings_and_csl_dicts() -> None:
    from pzi.commands.entries import _author_name

    assert _author_name("Smith, John") == "Smith, John"
    assert _author_name("  Doe, Jane  ") == "Doe, Jane"
    assert _author_name({"given": "Jane", "family": "Doe"}) == "Jane Doe"
    assert _author_name({"family": "Doe"}) == "Doe"
    assert _author_name(123) == ""


def test_export_refuses_to_overwrite_existing_output_without_force(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    output_path = tmp_path / "export.bib"
    original = "keep me"
    bib_path.write_text("@article{smith2024, title = {Test}}\n")
    output_path.write_text(original)
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    stderr = StringIO()

    exit_code = run_cli(
        ["export", "--config", str(config_path), "--output", str(output_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert output_path.read_text() == original
    assert "already exists" in stderr.getvalue()


def test_export_force_overwrites_existing_output(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    output_path = tmp_path / "export.bib"
    bib_path.write_text("@article{smith2024, title = {Test}}\n")
    output_path.write_text("old")
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    exit_code = run_cli(
        [
            "export",
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--force",
        ],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "smith2024" in output_path.read_text()


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
            "--pdf-candidate",
            "https://example.com/a.pdf",
            "--pdf-candidate",
            "https://example.com/b.pdf",
        ]
    )

    assert build_capture_input_from_add_args(args, bib_selector="ml") == CaptureInput(
        value="https://example.com/paper",
        record_overrides={},
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


def test_build_capture_options_from_add_args_reads_page_metadata_cmd_from_config() -> None:
    parser = __import__("pzi.cli", fromlist=["build_parser"]).build_parser()
    args = parser.parse_args(["add", "https://example.com/paper", "--dry-run"])

    assert build_capture_options_from_add_args(
        args,
        config={
            "page_metadata_cmd": "config-tool",
            "page_metadata_timeout_seconds": 11,
        },
    ) == CaptureOptions(
        dry_run=True,
        force_new=False,
        page_metadata_cmd="config-tool",
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


# === bulk capture: pzi add --from-file ===


def test_parse_batch_values_skips_comments_blanks_and_dedupes() -> None:
    text = "# header\n\nhttps://a/1\n  https://b/2  \nhttps://a/1\n# tail\n"
    assert parse_batch_values(text) == ["https://a/1", "https://b/2"]


def _batch_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path / "library.bib"}"\ndefault = true\n'
    )
    return config_path


def _fake_capture_factory():
    def fake_capture(capture_input, _options, *, config_path, home_dir, service_kwargs):
        value = capture_input.value
        if "bad" in value:
            return {"status": "error", "message": "could not resolve",
                    "errors": ["could not resolve"], "warnings": []}
        action = "update" if "dup" in value else "insert"
        return {"status": "ok", "action": action, "bib_name": "ml",
                "citekey": value.rsplit("/", 1)[-1], "warnings": [], "errors": []}
    return fake_capture


def test_add_from_file_captures_all_with_summary(tmp_path: Path, monkeypatch) -> None:
    import pzi.commands.add as add_module

    monkeypatch.setattr(add_module, "capture_to_bib", _fake_capture_factory())
    urls = tmp_path / "urls.txt"
    urls.write_text("# papers\nhttps://x/good1\nhttps://x/good2\n")
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_cli(
        ["add", "--from-file", str(urls), "--delay", "0", "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr, fetch_web=_fake_fetch_web,
    )

    assert exit_code == 0
    assert "2 added, 0 already present, 0 failed" in stdout.getvalue()
    assert not (tmp_path / "urls.failed.txt").exists()  # no failures -> no file


def test_add_from_file_writes_failures_and_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    import pzi.commands.add as add_module

    monkeypatch.setattr(add_module, "capture_to_bib", _fake_capture_factory())
    urls = tmp_path / "urls.txt"
    urls.write_text("https://x/good1\nhttps://x/dup1\nhttps://x/bad1\n")
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_cli(
        ["add", "--from-file", str(urls), "--delay", "0", "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr, fetch_web=_fake_fetch_web,
    )

    assert exit_code == 1
    assert "1 added, 1 already present, 1 failed" in stdout.getvalue()
    failures = tmp_path / "urls.failed.txt"
    assert failures.read_text() == "https://x/bad1\n"
    assert "could not resolve" in stderr.getvalue()


def test_add_requires_value_or_from_file(tmp_path: Path) -> None:
    stderr = StringIO()
    exit_code = run_cli(["add"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr)
    assert exit_code == 2
    assert "--from-file" in stderr.getvalue()


def test_add_rejects_value_combined_with_from_file(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("https://x/1\n")
    stderr = StringIO()
    exit_code = run_cli(
        ["add", "10.1/x", "--from-file", str(urls)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )
    assert exit_code == 2
    assert "not both" in stderr.getvalue()


def test_add_from_file_rejects_single_item_flags(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("https://x/1\n")
    stderr = StringIO()
    exit_code = run_cli(
        ["add", "--from-file", str(urls), "--citekey", "foo2024"],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )
    assert exit_code == 2
    assert "--citekey" in stderr.getvalue()


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

    meta = tmp_path / "meta.json"
    meta.write_text('{"title": "Graph Parsers"}')
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "10.1234/foo",
            "--metadata-json",
            str(meta),
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

    meta = tmp_path / "meta.json"
    meta.write_text('{"title": "Graph Parsers"}')
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "https://example.com/paper",
            "--metadata-json",
            str(meta),
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

    meta = tmp_path / "meta.json"
    meta.write_text('{"authors": ["Smith, Jane", "Doe, John"]}')
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "https://example.com/paper.pdf",
            "--citekey",
            "smith2024graph",
            "--metadata-json",
            str(meta),
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
    assert "pzi-pdf-url = {https://example.com/paper.pdf}" in contents


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

    meta = tmp_path / "meta.json"
    meta.write_text('{"title": "Graph Parsers", "authors": ["Smith, Jane"], "year": 2024}')
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        [
            "add",
            "https://example.com/paper",
            "--metadata-json",
            str(meta),
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
    # Secret hygiene: token lives in a separate 0600 file, referenced via
    # api_auth_token_cmd; config.toml (commonly committed) holds no raw token.
    token_file = tmp_path / ".local" / "share" / "pzi" / "api_token"
    assert token_file.exists()
    import stat as _stat
    assert _stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert f"api_auth_token_cmd = \"cat {token_file}\"" in config
    assert 'api_auth_token = "' not in config
    # config-only: guidance points at first-use bootstrap, no install ran
    out = stdout.getvalue()
    assert "playwright install" in out
    assert "pzi server" in out
    # regression: distribution is "paperazzi", not "pzi" (pzi is only the CLI
    # command) — pip/pipx install 'pzi[playwright]' installs the wrong package
    assert "paperazzi[playwright]" in out
    assert "'pzi[playwright]'" not in out


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


def test_doctor_reinstall_server_handles_missing_config(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()

    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []\n")

    exit_code = run_cli(
        ["doctor", "--reinstall-server", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 1
    assert "failed to load config" in stderr.getvalue()


@pytest.mark.parametrize("sub", ["status", "update", "up", "down"])
def test_cli_services_command_removed(tmp_path: Path, sub: str) -> None:
    """`pzi services …` is gone — health is `doctor`, reinstall is `doctor --reinstall-server`."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []\n")

    exit_code = run_cli(
        ["services", sub, "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    # argparse rejects the unknown command with exit code 2.
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
        ["entries", "--stats"],
        ["entries"],
        ["search", "--query", "graph"],
        ["tag", "list"],
        ["fix", "dedupe"],
        ["fix", "clean"],
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
    exit_code = run_cli(["entries", "--json"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "main" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_pdf_attach_dispatches_to_pdf_service(tmp_path: Path) -> None:
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

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(
        pdf_command="attach",
        citekey="smith2024graph",
        source="https://example.com/paper.pdf",
    )

    exit_code = run_pdf_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        bib_selector=None,
        stdout=stdout,
        stderr=stderr,
        attach_pdf_fn=fake_attach_pdf,
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


def test_cli_pdf_retry_dispatches_to_pdf_service(tmp_path: Path) -> None:
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

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(
        pdf_command="retry",
        citekey="smith2024graph",
        failed_only=False,
    )

    exit_code = run_pdf_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        bib_selector=None,
        stdout=stdout,
        stderr=stderr,
        retry_pdf_fn=fake_retry_pdf,
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


def test_cli_update_promote_dispatches_to_promote_service(tmp_path: Path) -> None:
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

    stdout = StringIO()
    stderr = StringIO()
    # `update --promote --replace` routes to the promotion service in place.
    args = Namespace(target=None, dry_run=False, replace=True, verbose=False, promote=True)

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        promote_bib_fn=fake_promote_bib,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": None,
            "dry_run": False,
            "keep_preprint": False,
            "mark_resolved": False,
        }
    ]
    assert stderr.getvalue() == ""


def test_cli_update_replace_without_promote_is_rejected(tmp_path: Path) -> None:
    stderr = StringIO()
    args = Namespace(target=None, dry_run=False, replace=True, verbose=False, promote=False)

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "--replace only applies with --promote" in stderr.getvalue()


def test_cli_update_mark_resolved_without_promote_is_rejected(tmp_path: Path) -> None:
    stderr = StringIO()
    args = Namespace(
        target=None, dry_run=False, replace=False, verbose=False,
        promote=False, mark_resolved=True,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "--mark-resolved only applies with --promote" in stderr.getvalue()


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


# === doctor --config-only (formerly `config validate`) ===


def test_doctor_config_only_validates_offline(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{tmp_path / "lib.bib"}"\ndefault = true\n'
    )
    stdout = StringIO()
    exit_code = run_cli(
        ["doctor", "--config-only", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=stdout, stderr=StringIO(),
    )
    assert exit_code == 0
    assert "config valid" in stdout.getvalue()


def test_doctor_config_only_reports_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = []")
    stderr = StringIO()
    exit_code = run_cli(
        ["doctor", "--config-only", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )
    assert exit_code == 1
    assert "config invalid" in stderr.getvalue()


# === reindex: read-only audit by default, rename only on opt-in ===


def _reindex_config(tmp_path: Path) -> tuple[Path, Path]:
    bib_path = tmp_path / "lib.bib"
    bib_path.write_text(
        "@article{oldkey,\n  author = {Smith, Jane},\n  title = {Graph Networks},\n"
        "  year = {2020},\n}\n"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    return config_path, bib_path


def test_reindex_default_is_read_only_audit(tmp_path: Path) -> None:
    config_path, bib_path = _reindex_config(tmp_path)
    before = bib_path.read_text()
    stdout = StringIO()
    exit_code = run_cli(
        ["fix", "reindex", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=stdout, stderr=StringIO(),
    )
    assert exit_code == 0
    assert bib_path.read_text() == before  # nothing renamed
    assert "@article{oldkey," in bib_path.read_text()
    assert "--rename-citekeys" in stdout.getvalue()


def test_reindex_rename_citekeys_applies_with_warning(tmp_path: Path) -> None:
    config_path, bib_path = _reindex_config(tmp_path)
    stderr = StringIO()
    exit_code = run_cli(
        ["fix", "reindex", "--rename-citekeys", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )
    assert exit_code == 0
    assert "@article{oldkey," not in bib_path.read_text()  # renamed
    assert "cite" in stderr.getvalue().lower()  # warned about \cite{}


# ---------------------------------------------------------------------------
# Bad-invocation error format + CLI robustness boundary
# ---------------------------------------------------------------------------


def test_invocation_error_has_no_usage_block(tmp_path: Path) -> None:
    """Bad-invocation errors are two lines: `prog: error: …` + help pointer."""
    stderr = StringIO()
    exit_code = run_cli(["add"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr)
    assert exit_code == 2
    assert stderr.getvalue() == (
        "pzi add: error: provide a DOI, URL, or PDF path, or use --from-file PATH\n"
        "Run 'pzi add --help' for usage.\n"
    )


def test_argparse_error_also_has_no_usage_block(tmp_path: Path) -> None:
    """argparse-native errors share the same compact format (no `usage:` line)."""
    stderr = StringIO()
    exit_code = run_cli(["delete"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr)
    assert exit_code == 2
    out = stderr.getvalue()
    assert not out.startswith("usage:")
    assert "pzi delete: error: the following arguments are required: citekey" in out
    assert out.rstrip().endswith("Run 'pzi delete --help' for usage.")


def test_negative_numeric_argument_is_rejected(tmp_path: Path) -> None:
    stderr = StringIO()
    exit_code = run_cli(
        ["entries", "--limit", "-5"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr
    )
    assert exit_code == 2
    assert "must be zero or greater" in stderr.getvalue()


def test_run_cli_converts_oserror_to_clean_error(tmp_path: Path, monkeypatch) -> None:
    """An unexpected OSError in a command becomes `error: …` + exit 1, not a traceback."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[[bibs]]\nname="ml"\npath="{tmp_path / "x.bib"}"\ndefault=true\n')

    def boom(*_a, **_k):
        raise PermissionError(13, "Permission denied", str(tmp_path / "x.bib"))

    monkeypatch.setattr(cli, "_run_entries", boom)
    stderr = StringIO()
    exit_code = run_cli(
        ["entries", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    # Friendly: no "[Errno 13]" noise, just the OS message + path.
    assert stderr.getvalue() == f"error: Permission denied: {tmp_path / 'x.bib'}\n"


def test_non_utf8_bib_gives_friendly_message(tmp_path: Path) -> None:
    bib_path = tmp_path / "bad.bib"
    bib_path.write_bytes(b"@article{x,\n title={caf\xe9}\n}\n")  # 0xe9 is not valid UTF-8
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[[bibs]]\nname="ml"\npath="{bib_path}"\ndefault=true\n')
    stderr = StringIO()
    exit_code = run_cli(
        ["entries", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=stderr,
    )
    assert exit_code == 1
    # Names the offending file so multi-bib users know which one to fix.
    assert stderr.getvalue() == f"error: {bib_path} is not valid UTF-8 text\n"


def test_friendly_error_renders_oserror_and_decode_errors() -> None:
    from pzi.cli import _friendly_error

    assert (
        _friendly_error(PermissionError(13, "Permission denied", "/x.bib"))
        == "Permission denied: /x.bib"
    )
    try:
        b"\xe9".decode("utf-8")
    except UnicodeDecodeError as exc:
        assert _friendly_error(exc) == "file is not valid UTF-8 text"


def test_main_handles_broken_pipe(monkeypatch) -> None:
    def _broken(*_a, **_k):
        raise BrokenPipeError

    monkeypatch.setattr(cli, "run_cli", _broken)
    monkeypatch.setattr(cli.os, "open", lambda *_a, **_k: -1)
    monkeypatch.setattr(cli.os, "dup2", lambda *_a, **_k: None)
    assert cli.main() == 141


def test_main_handles_keyboard_interrupt(monkeypatch) -> None:
    def _interrupt(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_cli", _interrupt)
    assert cli.main() == 130


def test_maybe_start_watchdog_skips_unowned_backend(dead_port) -> None:
    from pzi.commands import server as server_command

    backend = {"url": f"http://127.0.0.1:{dead_port}", "ready": True,
               "owned": False, "proc": object()}
    wd = server_command._maybe_start_watchdog(
        backend, stdout=StringIO(), stderr=StringIO()
    )
    assert wd is None


def test_maybe_start_watchdog_starts_for_owned_ready_backend(dead_port) -> None:
    from pzi.commands import server as server_command

    class _FakeProc:
        def poll(self):
            return None

    proc = _FakeProc()
    backend = {
        "url": f"http://127.0.0.1:{dead_port}", "ready": True, "owned": True, "proc": proc,
        "node_bin": "/usr/bin/node", "ts_dir": Path("/ts"), "port": dead_port,
        "stderr_log": None,
    }
    wd = server_command._maybe_start_watchdog(
        backend, stdout=StringIO(), stderr=StringIO()
    )
    assert wd is not None
    try:
        assert wd.current_proc is proc
    finally:
        wd.stop()  # joins the daemon thread; no real child to terminate


def test_run_cli_reports_concurrent_edit_without_traceback(monkeypatch) -> None:
    # A concurrent external edit aborts the write at the repository layer; the
    # CLI must render it as a friendly error and exit 1, not a raw traceback.
    from pzi.bib_repository import ConcurrentEditError

    def _raise(*_a, **_k):
        raise ConcurrentEditError("bib file was modified externally")

    monkeypatch.setattr(cli, "_run_add", _raise)
    stderr = StringIO()
    exit_code = run_cli(
        ["add", "https://example.com/paper", "--config", "/tmp/x.toml"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "modified externally" in stderr.getvalue()
    assert "retry" in stderr.getvalue()
