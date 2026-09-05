import importlib
import json
from argparse import ArgumentParser, Namespace, _SubParsersAction
from io import StringIO
from pathlib import Path

import pytest

import pzi.cli as cli
import pzi.commands.export as export_command
from pzi import exit_codes
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


def _only_provider_warnings(text: str) -> bool:
    """Every stderr line is a provider-failure warning, and nothing else.

    A title-less translation-server answer no longer stops the DOI cascade
    (finding A4: it used to shadow Crossref permanently). The hermetic suite
    blocks non-loopback sockets, so consulting the cascade now legitimately
    produces one `provider error (...)` warning per provider. That is the
    intended behaviour, not noise — but stderr must still carry nothing else,
    which is what these tests originally used `== ""` to say.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    return all(line.startswith("warning: provider error (") for line in lines)


def test_cli_dispatch_registry_covers_all_parser_commands() -> None:
    """Every parser command must be dispatchable, and vice versa.

    This asserts against `cli._DISPATCH` — the table `run_cli` actually indexes
    — not against a hand-written list. The previous version compared the parser
    to a literal `CLI_COMMANDS` tuple while the real dispatch dict was a local
    inside `run_cli` and unreachable from here, so a command added to the parser
    and the literal but not to the dispatch dict passed this test and then died
    at runtime with "unknown command".
    """
    parser_commands = _parser_command_names(build_parser())

    assert parser_commands == set(cli._DISPATCH)
    assert "library" in cli._DISPATCH
    # Removed top-level commands must not linger in the dispatch registry.
    # `fix` was renamed to `library` and `check` moved inside it (item 432), so
    # both are subcommands now and neither may still be dispatchable at the top
    # level — a stale entry there would keep the old spelling silently working.
    removed = {"fix", "check", "list", "set-default", "version", "clean",
               "dedupe", "merge", "reindex", "services"}
    assert not (removed & set(cli._DISPATCH))


def test_the_help_listing_names_every_command_and_no_others() -> None:
    """`_COMMAND_GROUPS` is the only command inventory a user ever sees.

    `_PziHelpFormatter` suppresses argparse's own subparser section, so the
    hand-written groups in `cli_parser` are the whole of `pzi --help`. Nothing
    checked them against the parser: a command could be added, dispatched and
    tested while being invisible to anyone reading the help — and a removed one
    could keep being advertised. This is the `_DISPATCH` check above applied to
    the surface a person actually reads.
    """
    from pzi.cli_parser import _COMMAND_GROUPS

    listed = [name for _title, cmds in _COMMAND_GROUPS for name, _desc in cmds]

    assert len(listed) == len(set(listed)), (
        f"a command is listed in two groups: {sorted({n for n in listed if listed.count(n) > 1})}"
    )
    assert set(listed) == _parser_command_names(build_parser()), (
        "pzi --help and the parser disagree about which commands exist"
    )
    assert all(desc.strip() for _t, cmds in _COMMAND_GROUPS for _n, desc in cmds), (
        "every listed command needs a description; the help renders it beside the name"
    )


def test_pdf_runner_name_matches_command_scope() -> None:
    assert hasattr(cli, "_run_pdf")
    assert not hasattr(cli, "_run_pdf_retry")


def test_export_runner_lives_in_command_module() -> None:
    assert cli._run_export is export_command.run_export_command


def test_import_runner_lives_in_command_module() -> None:
    import_command = importlib.import_module("pzi.commands.import_")

    assert cli._run_import is import_command.run_import_command


def test_library_runner_lives_in_command_module() -> None:
    library_command = importlib.import_module("pzi.commands.library")
    assert cli._run_library is library_command.run_library_command


def test_library_dispatches_to_every_subcommand() -> None:
    """The group's table, pinned — including `check`, which moved into it.

    `check` was top-level while three of the four maintenance subcommands were
    also read-only, so "does this inspect a library" and "where do I type it"
    disagreed (item 432).
    """
    library_command = importlib.import_module("pzi.commands.library")
    check_command = importlib.import_module("pzi.commands.check")
    clean_command = importlib.import_module("pzi.commands.clean")
    dedupe_command = importlib.import_module("pzi.commands.dedupe")
    bibs_command = importlib.import_module("pzi.commands.bibs")
    reindex_command = importlib.import_module("pzi.commands.reindex")
    assert library_command._SUBCOMMANDS == {
        "list": bibs_command.run_list_command,
        "check": check_command.run_check_command,
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
    for command in ("add", "search", "library", "doctor"):
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

    # USAGE, not 1: the command refused the invocation and did nothing, and the
    # exit-code contract reserves 1 for "ran fine, has something to report".
    assert exit_code == exit_codes.USAGE
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
    assert _only_provider_warnings(stderr.getvalue())
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

    assert exit_code == exit_codes.PARTIAL
    assert "1 added, 1 already present, 1 failed" in stdout.getvalue()
    failures = tmp_path / "urls.failed.txt"
    assert failures.read_text() == "https://x/bad1\n"
    assert "could not resolve" in stderr.getvalue()


def test_add_from_file_survives_one_raising_item(tmp_path: Path, monkeypatch) -> None:
    """An exception on item K must not discard items 1..K-1.

    Without a per-item guard the batch aborted: no summary, no failures file,
    and nothing to resume from — the successful captures already written to the
    bib went unreported.
    """
    import pzi.commands.add as add_module

    inner = _fake_capture_factory()

    def exploding_capture(capture_input, *args, **kwargs):
        if "boom" in capture_input.value:
            raise RuntimeError("provider exploded")
        return inner(capture_input, *args, **kwargs)

    monkeypatch.setattr(add_module, "capture_to_bib", exploding_capture)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://x/good1\nhttps://x/boom\nhttps://x/good2\n")
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_cli(
        ["add", "--from-file", str(urls), "--delay", "0",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr, fetch_web=_fake_fetch_web,
    )

    assert exit_code == exit_codes.PARTIAL
    # Both good items still counted, and the summary was printed.
    assert "2 added, 0 already present, 1 failed" in stdout.getvalue()
    assert "provider exploded" in stderr.getvalue()
    # The failing URL is recorded so the run can be resumed.
    assert (tmp_path / "urls.failed.txt").read_text() == "https://x/boom\n"


def test_add_from_file_dry_run_writes_no_failures_file(
    tmp_path: Path, monkeypatch
) -> None:
    """`--dry-run` prints "nothing will be written" — the failures file counts."""
    import pzi.commands.add as add_module

    monkeypatch.setattr(add_module, "capture_to_bib", _fake_capture_factory())
    urls = tmp_path / "urls.txt"
    urls.write_text("https://x/good1\nhttps://x/bad1\n")
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_cli(
        ["add", "--from-file", str(urls), "--dry-run", "--delay", "0",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr, fetch_web=_fake_fetch_web,
    )

    # A partly-failed batch is PARTIAL even in dry-run.
    assert exit_code == exit_codes.PARTIAL
    assert not (tmp_path / "urls.failed.txt").exists()


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
    assert _only_provider_warnings(stderr.getvalue())
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

    assert exit_code == exit_codes.ENVIRONMENT
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

    assert exit_code == exit_codes.ENVIRONMENT
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
    # Secret hygiene: token lives in a separate 0600 file that pzi auto-reads;
    # config.toml (commonly committed) holds neither the token nor its path.
    token_file = tmp_path / ".local" / "share" / "pzi" / "api_token"
    assert token_file.exists()
    import stat as _stat
    assert _stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert 'api_auth_token = "' not in config
    assert "\napi_auth_token_cmd = " not in config
    assert str(token_file) not in config
    # config-only: guidance points at first-use bootstrap, no install ran
    out = stdout.getvalue()
    assert "playwright install" in out
    assert "pzi server" in out
    # regression: the distribution is "pzi" — the same name as the CLI command —
    # since the 1.0 rename. `paperazzi` on PyPI is an unrelated package, so
    # guidance that prints that name sends the user to install someone else's.
    assert "'pzi[playwright]'" in out
    assert "paperazzi" not in out


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

    assert exit_code == exit_codes.ENVIRONMENT
    assert "failed to load config" in stderr.getvalue()


def test_doctor_reinstall_server_prints_when_node_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    """The non-JSON failure branch used to print nothing at this call site.

    Only the `config is None` caller worked around the gap with its own
    manual `print_lines`; `ensure_node` returning `None` reached `_finish`
    directly, whose non-JSON branch printed only on success — so this failure
    exited 5 with zero bytes on stderr.
    """
    import pzi.node_runtime

    monkeypatch.setattr(pzi.node_runtime, "ensure_node", lambda *a, **k: None)
    stderr = StringIO()

    exit_code = run_cli(
        ["doctor", "--reinstall-server", "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.ENVIRONMENT
    assert "Node.js is not available" in stderr.getvalue()


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


#: The five keys `README.md` promises on every `--json` document.
_ENVELOPE_KEYS = frozenset({"command", "status", "bib_name", "items", "errors"})


@pytest.mark.parametrize(
    "argv",
    [
        ["entries", "--stats"],
        ["entries"],
        ["search", "--query", "graph"],
        ["tag", "list"],
        ["library", "dedupe"],
        ["library", "clean"],
        ["doctor", "--config-only"],
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
    # `assert parsed is not None` passed for `{}` — assert the documented shape.
    assert _ENVELOPE_KEYS <= set(parsed), parsed
    assert isinstance(parsed["items"], list)


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
            # Off unless `--discover` is passed: the default derivation uses
            # only the pure steps, which make no network call.
            "deep": False,
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
    # `update --promote` routes to the promotion service, which replaces the
    # preprint in place by default.
    args = Namespace(
        target=None, dry_run=False, keep_preprint=False, verbose=False, promote=True,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        promote_bib_fn=fake_promote_bib,
    )

    assert exit_code == 0
    # `on_item` is a closure; the rest is pinned exactly, so a new kwarg on the
    # service still fails this test.
    assert all(callable(call.pop("on_item")) for call in calls)
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": None,
            "dry_run": False,
            "keep_preprint": False,
            "mark_resolved": False,
            "limit": None,
            "best_of": 1,
        }
    ]
    assert stderr.getvalue() == ""


def test_cli_update_best_of_is_rejected_without_promote(tmp_path: Path) -> None:
    stderr = StringIO()
    args = Namespace(
        target=None, dry_run=False, verbose=False, promote=False, best_of=3,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "--best-of only applies with --promote" in stderr.getvalue()


def test_cli_update_best_of_below_one_is_rejected(tmp_path: Path) -> None:
    """`--best-of 0` would mean "stop before looking", which is not a search."""
    stderr = StringIO()
    args = Namespace(
        target=None, dry_run=False, verbose=False, promote=True, best_of=0,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "--best-of must be at least 1" in stderr.getvalue()


def test_cli_update_best_of_reaches_the_service(tmp_path: Path) -> None:
    """The runner reads it with `getattr`, so pin that it arrives."""
    calls: list[dict] = []

    def fake_promote_bib(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok", "bib_name": "ml", "dry_run": kwargs["dry_run"],
            "items": [], "warnings": [], "errors": [],
        }

    args = Namespace(
        target=None, dry_run=False, keep_preprint=False, verbose=False,
        promote=True, best_of=5,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        promote_bib_fn=fake_promote_bib,
    )

    assert exit_code == 0
    assert [call["best_of"] for call in calls] == [5]


def test_cli_update_keep_preprint_reaches_the_service(tmp_path: Path) -> None:
    # The default flipped to replace-in-place, so the only thing that can still
    # select keep-both is this flag. Nothing else pinned that it arrives as
    # `True`, and the runner reads it with `getattr`, so a rename on either side
    # would silently downgrade every `--keep-preprint` run to a replace.
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

    args = Namespace(
        target=None, dry_run=False, keep_preprint=True, verbose=False, promote=True,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        promote_bib_fn=fake_promote_bib,
    )

    assert exit_code == 0
    assert [call["keep_preprint"] for call in calls] == [True]


def test_cli_update_keep_preprint_without_promote_is_rejected(tmp_path: Path) -> None:
    stderr = StringIO()
    args = Namespace(
        target=None, dry_run=False, keep_preprint=True, verbose=False, promote=False,
    )

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "--keep-preprint only applies with --promote" in stderr.getvalue()


def test_cli_update_mark_resolved_without_promote_is_rejected(tmp_path: Path) -> None:
    stderr = StringIO()
    args = Namespace(
        target=None, dry_run=False, keep_preprint=False, verbose=False,
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
    assert exit_code == exit_codes.ENVIRONMENT
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
        ["library", "reindex", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=stdout, stderr=StringIO(),
    )
    # A read-only audit that found renames to make has something to report.
    assert exit_code == exit_codes.FINDINGS
    assert bib_path.read_text() == before  # nothing renamed
    assert "@article{oldkey," in bib_path.read_text()
    assert "--rename-citekeys" in stdout.getvalue()


def test_reindex_rename_citekeys_applies_with_warning(tmp_path: Path) -> None:
    config_path, bib_path = _reindex_config(tmp_path)
    stderr = StringIO()
    exit_code = run_cli(
        ["library", "reindex", "--rename-citekeys", "--force", "--config", str(config_path)],
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
    """`--offset` still accepts 0, so it keeps the non-negative validator."""
    stderr = StringIO()
    exit_code = run_cli(
        ["entries", "--offset", "-5"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr
    )
    assert exit_code == 2
    assert "must be zero or greater" in stderr.getvalue()


def test_entries_limit_zero_is_a_usage_error(tmp_path: Path) -> None:
    """`--limit 0` was silently clamped to 1.

    The result envelope then reported `"limit": 1` — a different limit from the
    one requested, with nothing said about it.
    """
    stderr = StringIO()
    exit_code = run_cli(
        ["entries", "--limit", "0"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr
    )
    assert exit_code == 2
    assert "must be one or greater" in stderr.getvalue()


def test_entries_limit_negative_is_a_usage_error(tmp_path: Path) -> None:
    stderr = StringIO()
    exit_code = run_cli(
        ["entries", "--limit", "-5"], home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr
    )
    assert exit_code == 2
    assert "must be one or greater" in stderr.getvalue()


@pytest.mark.parametrize("value", ["0", "-3"])
def test_update_promote_limit_below_one_is_a_parser_usage_error(
    tmp_path: Path, value: str
) -> None:
    """`--limit` is `_positive_int` at the parser, matching `entries --limit`.

    This used to be a runner-side check (`commands/update.py`) that produced
    the same exit code but, under `--json`, a full envelope — the parser
    rejection documented as the one exception to "every `--json` command
    always emits one document" writes nothing to stdout even with `--json`
    passed, so this is a real CLI-surface change: `--limit 0 --json` no
    longer emits an envelope.
    """
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["update", "--promote", "--limit", value, "--json",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )
    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "must be one or greater" in stderr.getvalue()


@pytest.mark.parametrize("value", ["0", "-3"])
def test_library_check_limit_below_one_is_a_parser_usage_error(
    tmp_path: Path, value: str
) -> None:
    """Same unification as `update --limit` above, for `library check --limit`.

    Before this, `--limit 0` audited nothing and exited `0` — a clean bill of
    health for a run that checked zero entries — and `--limit -1` meant
    "unlimited" to `check_service.check_bib`, silently auditing the whole
    library. The parser now rejects both before either service call.
    """
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["library", "check", "--limit", value, "--json",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )
    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "must be one or greater" in stderr.getvalue()


def test_run_cli_converts_oserror_to_clean_error(tmp_path: Path, monkeypatch) -> None:
    """An unexpected OSError becomes `error: …` + the environment exit code."""
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
    assert exit_code == exit_codes.ENVIRONMENT
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
    assert exit_code == exit_codes.ENVIRONMENT
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
    # `ConcurrentEditError` is a `RuntimeError`, so the CLI's RuntimeError
    # boundary renders it: its own message, exit 5, no traceback. It had a
    # dedicated arm until that arm turned out to be both unreachable — the only
    # raiser, `promote_service`, converts it into a per-preprint failure — and
    # byte-for-byte identical to the boundary below it. This pins the boundary,
    # not the (injected) route: what matters is that a refusal carrying a
    # citekey reaches the user with that citekey in it.
    from pzi.bib_repository import ConcurrentEditError

    def _raise(*_a, **_k):
        raise ConcurrentEditError(
            "citekey smith2024graph appeared in /tmp/lib.bib while promoting "
            "smith2024graph-preprint; aborting rather than writing a duplicate entry"
        )

    monkeypatch.setattr(cli, "_run_add", _raise)
    stderr = StringIO()
    exit_code = run_cli(
        ["add", "https://example.com/paper", "--config", "/tmp/x.toml"],
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == exit_codes.ENVIRONMENT
    assert "Traceback" not in stderr.getvalue()
    assert "aborting rather than writing a duplicate entry" in stderr.getvalue()
    assert "smith2024graph" in stderr.getvalue()


def test_init_creates_config_owner_only(tmp_path: Path) -> None:
    """config.toml may hold a plaintext token and *_cmd hooks pzi executes.

    It was written with the default umask mode, so on a typical system it landed
    group/world-readable and `pzi doctor` then warned about the file pzi itself
    had just created.
    """
    import stat

    config_path = tmp_path / "config.toml"
    exit_code = run_cli(
        ["init", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert exit_code == 0
    mode = stat.S_IMODE(config_path.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_add_from_file_prints_per_item_warnings(tmp_path: Path, monkeypatch) -> None:
    """The same duplicate notice must reach the user in bulk `add` too."""
    import pzi.commands.add as add_module

    def warning_capture(_capture_input, *_args, **_kwargs):
        return {
            "status": "ok", "action": "insert", "bib_name": "ml",
            "citekey": "smith2024", "errors": [],
            "warnings": ["probable duplicate of smith2020 (title 97% similar)"],
        }

    monkeypatch.setattr(add_module, "capture_to_bib", warning_capture)
    urls = tmp_path / "urls.txt"
    urls.write_text("https://x/good1\n")
    stdout, stderr = StringIO(), StringIO()

    run_cli(
        ["add", "--from-file", str(urls), "--delay", "0",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr, fetch_web=_fake_fetch_web,
    )

    assert "probable duplicate of smith2020" in stderr.getvalue()


def test_add_malformed_metadata_json_is_a_usage_error(tmp_path: Path) -> None:
    """A JSON typo is user input, not a pzi bug — it must not traceback.

    `load_add_metadata_json` raised `json.JSONDecodeError` (a `ValueError`),
    which the CLI boundary deliberately does not catch.
    """
    bad = tmp_path / "meta.json"
    bad.write_text('{"title": "Unclosed')
    stderr = StringIO()

    exit_code = run_cli(
        ["add", "10.1234/x", "--metadata-json", str(bad),
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.USAGE
    out = stderr.getvalue()
    assert "not valid JSON" in out
    assert "Traceback" not in out


def test_add_non_object_metadata_json_is_a_usage_error(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    meta.write_text('["not", "an", "object"]')
    stderr = StringIO()

    exit_code = run_cli(
        ["add", "10.1234/x", "--metadata-json", str(meta),
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.USAGE
    assert "must contain a JSON object" in stderr.getvalue()


def test_add_rejects_bad_metadata_json_before_starting_the_backend(
    tmp_path: Path, monkeypatch
) -> None:
    """The check must run before `backend_session`, not inside it.

    Parsing at capture time meant a one-character typo cost a full Node /
    translation-server startup — and printed `starting translation-server` —
    before failing.
    """
    import pzi.ts_backend

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("backend_session started before validating input")

    monkeypatch.setattr(pzi.ts_backend, "backend_session", _must_not_run)

    bad = tmp_path / "meta.json"
    bad.write_text("{oops")
    stderr = StringIO()

    exit_code = run_cli(
        ["add", "10.1234/x", "--metadata-json", str(bad),
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.USAGE
    assert "starting translation-server" not in stderr.getvalue()


def test_doctor_health_problems_flags_a_broken_key_command() -> None:
    """Naming the fault and still exiting 0 is the outcome doctor exists to prevent.

    Semantic Scholar was not part of the health verdict at all, so a recorded
    `key_error` would have printed and still exited 0. An unreachable API stays
    advisory — that is not the user's config being wrong.
    """
    from pzi.doctor_service import doctor_health_problems

    healthy = {"config_ok": True, "bibs": [{"path_exists": True}],
               "translation_server_url": None, "semantic_scholar": {}}
    assert doctor_health_problems(healthy) == []

    broken_key = {**healthy, "semantic_scholar": {"key_error": "secret command exited with code 1"}}
    assert doctor_health_problems(broken_key) != []

    unreachable_api = {**healthy, "semantic_scholar": {"probe_error": "connection refused"}}
    assert doctor_health_problems(unreachable_api) == []


def test_add_reports_a_broken_key_command_as_a_structured_error(tmp_path: Path) -> None:
    """`add` already guarded this; changing the exception type must not regress it.

    `add_service` catches around `build_capture_context` and returns a structured
    error result — the HTTP capture route consumes that return value, so a
    `PziError` escaping the service layer would surface as a 500 rather than a
    reported error.
    """
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'semantic_scholar_api_key_cmd = "false"\n\n'
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_cli(
        ["add", "10.1234/x", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
        fetch_web=_fake_fetch_web, fetch_search=_fake_fetch_search,
    )

    out = stdout.getvalue() + stderr.getvalue()
    assert "Traceback" not in out
    assert exit_code == exit_codes.ENVIRONMENT
    # The service-level wrapper survives, proving the error was handled inside
    # the service rather than escaping to the CLI boundary.
    assert "failed to resolve capture context" in out
    # And it names the key. It used to say "secret command", which could have
    # been any of five `*_cmd` config lines — `resolve_optional_value` takes a
    # `config_key` for exactly this and `build_capture_context` called it
    # without one.
    assert "semantic_scholar_api_key_cmd" in out


# --- exit-code contract (Batch 5) -------------------------------------------


def test_import_missing_source_is_usage_not_not_found(tmp_path: Path) -> None:
    """`3` is reserved for a missing *entry*, not a missing file.

    It has to agree with `import_service`, which classifies a source path that
    is not there as `REASON_USAGE` -> 2. The runner used to check the same
    condition itself, emit that same reason and return 5, so which of the two
    checks won the race decided the exit status.
    """
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    stderr = StringIO()

    exit_code = run_cli(
        ["import", str(tmp_path / "nope.bib"), "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.USAGE
    assert "file not found" in stderr.getvalue()


def test_import_unknown_target_is_environment_not_partial(tmp_path: Path) -> None:
    """Nothing ran, so it is not a batch that partly failed.

    The resolution failure used to be fanned out into one error per record, so
    the run reported `ok` with N errors for one root cause and exited 4.
    """
    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    source = tmp_path / "source.bib"
    source.write_text("@article{a2024, title = {A}, doi = {10.1/a}}\n")
    stderr = StringIO()

    exit_code = run_cli(
        ["import", str(source), "--target", "nosuchbib", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.ENVIRONMENT


def test_add_backend_not_ready_is_environment(tmp_path: Path, monkeypatch) -> None:
    """"Could not run" is 5; `1` would mean the command ran and had findings."""
    import contextlib

    import pzi.ts_backend

    @contextlib.contextmanager
    def not_ready(*_args, **_kwargs):
        yield {"ready": False}

    monkeypatch.setattr(pzi.ts_backend, "backend_session", not_ready)
    stderr = StringIO()

    exit_code = run_cli(
        ["add", "10.1234/x", "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert exit_code == exit_codes.ENVIRONMENT
    assert "translation server is not running" in stderr.getvalue()


def test_add_from_file_unreadable_is_a_usage_error(tmp_path: Path) -> None:
    """The path was typed wrong, so the user must retype rather than retry.

    It reported `"reason": "usage"` on stdout and exited 5 — retype *and*
    retry — for one mistake.
    """
    stderr = StringIO()

    exit_code = run_cli(
        ["add", "--from-file", str(tmp_path / "missing.txt"), "--delay", "0",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
        fetch_web=_fake_fetch_web,
    )

    assert exit_code == exit_codes.USAGE


def test_add_from_file_checks_the_path_before_starting_the_backend(
    tmp_path: Path, monkeypatch
) -> None:
    """A typo'd list paid for a full translation-server bootstrap first.

    `--metadata-json` is validated in the fail-fast block for exactly this
    reason and `--from-file` was not, so the cheapest possible mistake was the
    most expensive one to be told about. No injected fetcher here on purpose:
    that is what makes `run_add_command` enter the backend session at all.
    """
    import contextlib

    import pzi.ts_backend

    entered = []

    @contextlib.contextmanager
    def _record(*_args, **_kwargs):
        entered.append(True)
        yield {"ready": True}

    monkeypatch.setattr(pzi.ts_backend, "backend_session", _record)

    exit_code = run_cli(
        ["add", "--from-file", str(tmp_path / "missing.txt"), "--delay", "0",
         "--config", str(_batch_config(tmp_path))],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=StringIO(),
    )

    assert exit_code == exit_codes.USAGE
    assert entered == [], "the backend session was started for a path that is not there"


@pytest.mark.parametrize(
    "argv",
    [
        # Unknown --target: raised as PziError from `resolve_target`, which is
        # the first statement of seven runners.
        ["library", "clean", "--target", "nosuchbib"],
        ["library", "dedupe", "--target", "nosuchbib"],
        ["library", "reindex", "--target", "nosuchbib"],
        ["entries", "--stats", "--target", "nosuchbib"],
        # `export` is deliberately absent: it has no `--json` flag (it uses
        # `--format json`), so argparse rejects the invocation before dispatch.
        # Recorded in PLAN.md as a separate decision.
        # Unknown citekey: a service-level error result.
        ["entries", "nosuch2024"],
        ["tag", "add", "nosuch2024", "ml"],
        ["tag", "remove", "nosuch2024", "ml"],
        ["pdf", "retry", "nosuch2024"],
        # Conditional usage errors argparse cannot express.
        ["search"],
        ["update", "--keep-preprint"],
        ["pdf", "retry"],
        ["library", "check", "--jsonl", "-"],
        # Missing input file.
        ["import", "/nonexistent/source.bib"],
    ],
)
def test_cli_failing_commands_still_emit_one_envelope(
    tmp_path: Path, argv: list[str]
) -> None:
    """`--json` promises a document *including when the command fails*.

    Every one of these used to print prose to stderr and emit nothing at all, so
    a script had to scrape stderr to classify the failure — the exact thing the
    contract says it never has to do. No test asserted the envelope on a failure
    path, which is why they drifted.
    """
    import json

    config = _write_small_library(tmp_path)
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_cli(
        [*argv, "--json", "--config", str(config)],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )

    assert exit_code != 0, "expected a failing invocation"
    raw = stdout.getvalue()
    assert raw.strip(), f"no JSON emitted for {argv}"
    parsed = json.loads(raw)  # exactly one document — raises on NDJSON or prose
    assert _ENVELOPE_KEYS <= set(parsed), parsed
    assert parsed["status"] == "error", parsed
    assert parsed["errors"], "the documented failure channel must not be empty"


def test_plain_init_writes_the_api_token_its_template_promises(tmp_path: Path) -> None:
    """The copied template says "`pzi init` writes a token to <data-home>/api_token
    (0600)", but the plain path never created one — so the common case shipped a
    config asserting a file that did not exist."""
    config_path = tmp_path / "config.toml"
    stdout = StringIO()

    exit_code = run_cli(
        ["init", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=stdout, stderr=StringIO(),
    )

    assert exit_code == exit_codes.OK
    from pzi.config import default_data_home

    token_path = Path(default_data_home(str(tmp_path))) / "api_token"
    assert token_path.is_file(), "plain `init` did not provision the token"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert token_path.read_text().strip(), "token file is empty"
    assert "api_token" in stdout.getvalue()


def test_no_bibtexparser_warning_leaks_to_the_terminal(tmp_path: Path) -> None:
    """`Unknown block type <class '...DuplicateBlockKeyBlock'>` reached the user.

    It is bibtexparser's own logger.warning, printed bare by Python's
    `lastResort` handler because pzi configured no logging at all. It appeared
    during an ordinary `pzi entries` run on a library with a duplicate citekey.

    Run in a subprocess deliberately: pytest's logging plugin installs a root
    handler, which suppresses `lastResort` on its own — so an in-process version
    of this test passes whether or not the fix is present, and proves nothing.
    """
    import subprocess
    import sys

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{smith2024,\n  title = {First}\n}\n"
        "@article{smith2024,\n  title = {Second}\n}\n"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pzi.cli import run_cli; "
            "sys.exit(run_cli(['entries', '--config', sys.argv[1]]))",
            str(config_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "Unknown block type" not in proc.stdout
    assert "Unknown block type" not in proc.stderr
    # The command still works; this is about the stray log line only.
    assert "smith2024" in proc.stdout


def _dup_library(tmp_path: Path) -> Path:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{smith2024,\n  title = {First}\n}\n"
        "@article{smith2024,\n  title = {Second}\n}\n"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    return config_path


@pytest.mark.parametrize(
    "argv",
    [
        ["entries"],
        ["entries", "--stats"],
        ["entries", "smith2024"],
        ["search", "--query", "first"],
        ["library", "dedupe"],
    ],
)
def test_read_commands_report_a_dropped_duplicate(tmp_path: Path, argv) -> None:
    """A duplicate citekey kept only the first block, silently.

    `pzi entries` said "1-1 of 1 entries" for a two-entry file, and `fix
    dedupe` — the command whose job is finding duplicates — reported zero
    clusters. The read still succeeds; it just has to say what it lost.
    """
    config_path = _dup_library(tmp_path)
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_cli(
        [*argv, "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code in (exit_codes.OK, exit_codes.FINDINGS)
    combined = stderr.getvalue()
    assert "duplicate citekey 'smith2024'" in combined, combined
    assert "at line 4" in combined
    # Still shows the data it could read.
    assert "smith2024" in stdout.getvalue() + stderr.getvalue()


def test_library_clean_json_populates_errors_on_an_unreadable_library(tmp_path: Path) -> None:
    """`errors[]` is the documented failure channel and must not be empty.

    `library clean` reported `"status": "error"` with `"errors": []`, stranding the
    detail in `issues[]` — the same defect the doctor command had. It is not
    covered by the parametrized envelope test above, which shares one valid
    library and so never reaches this path.
    """
    import json

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{ok2024,\n  title = {Fine}\n}\n@article{broken\n  title = {No}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )
    stdout = StringIO()

    exit_code = run_cli(
        ["library", "clean", "--json", "--config", str(config_path)],
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code != exit_codes.OK
    parsed = json.loads(stdout.getvalue())
    assert parsed["status"] == "error"
    assert parsed["errors"], "the documented failure channel must not be empty"
    assert "unparseable" in parsed["errors"][0]


def test_library_clean_reports_a_partly_read_library_the_same_way_in_both_formats(
    tmp_path: Path,
) -> None:
    """The verdict changed on 2026-08-23; the format-independence did not.

    This test used to assert exit 5, on the argument that 1 would make a broken
    library indistinguishable from a healthy one with findings. That argument was
    weighed and rejected: `partial_parse` and the issue severity already draw
    that line, and eight exit codes cannot. 5 says "could not run", which is
    false — the audit ran, and found that part of the file is unreadable.

    What has to stay true, and is the reason this test exists, is that one
    invocation does not tell a script and a human different things.
    """
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{broken\n  title = {No}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    stderr = StringIO()
    exit_code = run_cli(
        ["library", "clean", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )
    assert exit_code == exit_codes.FINDINGS
    # The reason reaches a terminal user, which under exit 5 it did not.
    assert "unparseable" in stderr.getvalue()

    json_stdout = StringIO()
    json_exit = run_cli(
        ["library", "clean", "--json", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=json_stdout, stderr=StringIO(),
    )
    assert json_exit == exit_code, "the exit code must not depend on --json"
    # Where the broken-vs-findings distinction actually lives.
    assert json.loads(json_stdout.getvalue())["partial_parse"] is True


def test_library_clean_exits_findings_for_a_duplicate_citekey(tmp_path: Path) -> None:
    """A duplicate is a finding: the library is readable, one entry is missing."""
    config_path = _dup_library(tmp_path)

    exit_code = run_cli(
        ["library", "clean", "--config", str(config_path)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=StringIO(),
    )

    assert exit_code == exit_codes.FINDINGS
