"""Doing something other than what was typed is worse than refusing.

Every flag here was accepted and then ignored, and every preview here described
a write the real run does not perform.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from pzi import exit_codes
from pzi.cli import run_cli

MINIMAL_CONFIG = """
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
"""


def _library(tmp_path: Path, text: str = "") -> tuple[Path, Path]:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(text, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")
    return config_path, bib_path


def _run(argv: list[str], tmp_path: Path) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    code = run_cli(argv, home_dir=str(tmp_path), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


# ---------------------------------------------------------------------------
# Flags that were accepted and ignored
# ---------------------------------------------------------------------------


def test_entries_citekey_with_stats_is_a_usage_error(tmp_path: Path) -> None:
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, stdout, stderr = _run(
        ["entries", "a1", "--stats", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--stats" in stderr
    assert stdout == ""


def test_add_delay_outside_batch_mode_is_a_usage_error(tmp_path: Path) -> None:
    config_path, _bib = _library(tmp_path)

    code, _stdout, stderr = _run(
        ["add", "10.1/x", "--delay", "5", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--delay" in stderr


def test_doctor_config_only_with_reinstall_server_is_a_usage_error(
    tmp_path: Path,
) -> None:
    config_path, _bib = _library(tmp_path)

    code, _stdout, stderr = _run(
        ["doctor", "--config-only", "--reinstall-server", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.USAGE
    assert "--config-only" in stderr


def test_config_may_precede_the_subcommand(tmp_path: Path) -> None:
    """`pzi --config X entries` failed with `argument command: invalid choice:
    '/path.toml'` — argparse reading the path as the command name."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, stdout, _stderr = _run(
        ["--config", str(config_path), "entries"], tmp_path
    )

    assert code == exit_codes.OK
    assert "a1" in stdout


def test_a_nonexistent_target_path_is_an_environment_error(tmp_path: Path) -> None:
    """`--target typo.bib` reported `entries: 0` at exit 0, indistinguishable
    from a clean library; README promises exit 5."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, _stdout, _stderr = _run(
        ["entries", "--target", str(tmp_path / "typo.bib"), "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.ENVIRONMENT


def test_export_to_dash_writes_to_stdout(tmp_path: Path) -> None:
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, stdout, _stderr = _run(
        ["export", "-o", "-", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.OK
    assert "@article{a1," in stdout
    assert not (Path.cwd() / "-").exists()


def test_entries_offset_past_the_end_still_reports_the_total(tmp_path: Path) -> None:
    config_path, _bib = _library(
        tmp_path, "@article{a1,\n  title = {A},\n}\n@article{b1,\n  title = {B},\n}\n"
    )

    _code, _stdout, stderr = _run(
        ["entries", "--offset", "99", "--config", str(config_path)], tmp_path
    )

    assert "2" in stderr  # the total, not a bare "(no entries)"


def test_add_force_new_is_registered(tmp_path: Path) -> None:
    """The capture path has always read `force_new` and the extension exposes
    it, but it was registered only on `import`."""
    from pzi.cli_parser import build_parser

    args = build_parser().parse_args(["add", "10.1/x", "--force-new"])

    assert args.force_new is True


# ---------------------------------------------------------------------------
# Reported values
# ---------------------------------------------------------------------------


def test_entries_json_reports_the_real_entry_type(tmp_path: Path) -> None:
    """It read `entry_type` off a *normalized record*, which never carries one,
    so every entry was reported as "unknown"."""
    import json

    config_path, _bib = _library(
        tmp_path,
        "@inproceedings{a1,\n  title = {A},\n}\n@article{b1,\n  title = {B},\n}\n",
    )

    _code, stdout, _stderr = _run(
        ["entries", "--json", "--config", str(config_path)], tmp_path
    )

    types = {item["citekey"]: item["entry_type"] for item in json.loads(stdout)["items"]}
    assert types == {"a1": "inproceedings", "b1": "article"}


def test_an_explicit_empty_host_list_is_not_the_default_list() -> None:
    """`x or DEFAULT` read an explicitly configured `[]` as "unset" and
    re-expanded the built-in list, so the browser hook still launched."""
    from pzi.pdf import _auto_browser_pdf_cmd_for_url

    assert (
        _auto_browser_pdf_cmd_for_url(
            "https://www.sciencedirect.com/science/article/pii/X",
            desktop_fallback_hosts=set(),
        )
        is None
    )


def test_an_unknown_config_key_is_reported(tmp_path: Path) -> None:
    """A typo in `capture_source_dirs` or `pdf_file_path_style` silently
    reverted to the default with no diagnostic."""
    from pzi.config import load_config_file

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'pdf_file_path_stile = "relative"\n\n[[bibs]]\nname = "ml"\n'
        f'path = "{tmp_path / "ml.bib"}"\ndefault = true\n',
        encoding="utf-8",
    )

    result = load_config_file(str(config_path), home_dir=str(tmp_path))

    assert result["config"] is not None  # a warning, not a failure
    assert result["warnings"] == ["unknown config key 'pdf_file_path_stile' (ignored)"]


# ---------------------------------------------------------------------------
# Previews that describe a different write
# ---------------------------------------------------------------------------


def test_promote_replace_dry_run_previews_an_in_place_rewrite(tmp_path: Path) -> None:
    """`_merge_published_metadata` strips `arxiv_id`, so identity matching found
    nothing and the preview showed an INSERT — telling the user their original
    entry survives when `--replace` overwrites it."""
    from pzi.promote_service import promote_bib

    config_path, bib_path = _library(
        tmp_path,
        "@unpublished{smith2024,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "  pages = {1--12},\n"
        "}\n",
    )

    def _search(query: str, *, server_url: str):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "venue": "JMLR",
                    "doi": "10.1234/published",
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        keep_preprint=False,
        fetch_search=_search,
    )

    item = result["items"][0]
    diff = item.get("diff") or ""
    assert item["action"] == "update"
    added_entries = [line for line in diff.splitlines() if line.startswith("+@")]
    # The one entry is retyped in place — not a second one appended.
    assert added_entries == ["+@article{smith2024,"], diff
    assert "-@unpublished{smith2024," in diff
    # And the unmodelled field is not shown as deleted.
    assert "-  pages" not in diff


def test_promote_keep_dry_run_previews_both_writes(tmp_path: Path) -> None:
    """Keep mode writes the published entry *and* stamps a cross-reference note
    onto the preprint; the preview showed only the first."""
    from pzi.promote_service import promote_bib

    config_path, _bib = _library(
        tmp_path,
        "@unpublished{smith2024,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n",
    )

    def _search(query: str, *, server_url: str):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "authors": ["Smith, Jane"],
                    "year": 2024,
                    "venue": "JMLR",
                    "doi": "10.1234/published",
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        keep_preprint=True,
        fetch_search=_search,
    )

    diff = result["items"][0].get("diff") or ""
    assert "Published version:" in diff, diff


# ---------------------------------------------------------------------------
# Destructive commands ask first
# ---------------------------------------------------------------------------

_RENAMEABLE = "@article{oldkey,\n  title = {New Test},\n  author = {Doe, John},\n  year = {2025},\n}\n"


def test_reindex_rename_without_force_refuses_when_stdin_is_not_a_terminal(
    tmp_path: Path,
) -> None:
    """It rewrites every citekey in the library, breaking `\\cite{}` outside pzi.

    `delete` and `fix merge` both confirm first; this one applied straight away,
    so a mistyped command was unrecoverable.
    """
    config_path, bib = _library(tmp_path, _RENAMEABLE)

    code, _stdout, stderr = _run(
        ["fix", "reindex", "--rename-citekeys", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--force" in stderr
    assert bib.read_text(encoding="utf-8") == _RENAMEABLE


def test_reindex_rename_with_force_rewrites_and_leaves_a_backup(tmp_path: Path) -> None:
    config_path, bib = _library(tmp_path, _RENAMEABLE)

    code, _stdout, stderr = _run(
        ["fix", "reindex", "--rename-citekeys", "--force", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.OK
    assert "oldkey" not in bib.read_text(encoding="utf-8")
    assert "backup saved to" in stderr
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _RENAMEABLE


def test_reindex_audit_needs_no_confirmation(tmp_path: Path) -> None:
    """The default run changes nothing, so it must not demand --force."""
    config_path, bib = _library(tmp_path, _RENAMEABLE)

    code, stdout, _stderr = _run(
        ["fix", "reindex", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.FINDINGS
    assert "--rename-citekeys" in stdout
    assert bib.read_text(encoding="utf-8") == _RENAMEABLE


def test_fix_clean_does_not_quarantine_a_sibling_librarys_pdf(tmp_path: Path) -> None:
    """The default layout points every configured bib at one `papers_dir`.

    `pzi fix clean --fix` on one target moved the other library's PDFs into
    `.orphans/`, leaving that library's `file =` fields dangling — for a user who
    simply ran the command against the wrong `--target`.
    """
    papers = tmp_path / "papers"
    papers.mkdir()
    theirs = papers / "theirs2024.pdf"
    theirs.write_bytes(b"%PDF-1.4\nTHEIRS\n")

    ml = tmp_path / "ml.bib"
    cs = tmp_path / "cs.bib"
    ml.write_text("@article{mine2024,\n  title = {Mine},\n}\n", encoding="utf-8")
    cs.write_text(
        f"@article{{theirs2024,\n  title = {{Theirs}},\n  file = {{{theirs}}},\n}}\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{ml}"\npapers_dir = "{papers}"\ndefault = true\n\n'
        f'[[bibs]]\nname = "cs"\npath = "{cs}"\npapers_dir = "{papers}"\n',
        encoding="utf-8",
    )

    code, _stdout, _stderr = _run(
        ["fix", "clean", "--fix", "--target", "ml", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.OK
    assert theirs.exists()
    assert not (papers / ".orphans").exists()


def test_entries_output_stays_five_tab_separated_columns(tmp_path: Path) -> None:
    """A tab or newline in a captured title used to shift or invent columns."""
    config_path, _bib = _library(
        tmp_path,
        "@article{evil2024,\n"
        "  title = {Real Title\tforged\nevil2025\t2025\tForged},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "}\n",
    )

    code, stdout, _stderr = _run(["entries", "--config", str(config_path)], tmp_path)

    assert code == exit_codes.OK
    rows = [line for line in stdout.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0].count("\t") == 4


# ---------------------------------------------------------------------------
# Flags that were accepted on some subpaths and ignored on others
# ---------------------------------------------------------------------------


def test_doctor_reinstall_server_json_emits_an_envelope(tmp_path: Path) -> None:
    """`--json` is documented for `doctor`, and `--reinstall-server` returns
    before the JSON branch — so this one subpath printed prose to stdout."""
    import json

    from pzi import ts_backend

    config_path, _bib = _library(tmp_path)
    ts_dir = tmp_path / ".local" / "share" / "pzi" / "ts"
    ts_dir.mkdir(parents=True)
    (ts_dir / "someone-elses-work.txt").write_text("x", encoding="utf-8")

    original = ts_backend.ensure_translation_server
    try:
        code, stdout, _stderr = _run(
            ["doctor", "--reinstall-server", "--json", "--config", str(config_path)],
            tmp_path,
        )
    finally:
        ts_backend.ensure_translation_server = original

    envelope = json.loads(stdout)
    assert envelope["command"].startswith("doctor")
    assert envelope["status"] in {"ok", "error"}
    assert code in {exit_codes.OK, exit_codes.ENVIRONMENT}


def test_check_report_to_stdout_conflicts_with_json(tmp_path: Path) -> None:
    """Both write to stdout, so together they produce neither a valid report nor
    the single document `--json` promises. `--jsonl -` already had this guard."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, stdout, stderr = _run(
        ["check", "--report", "-", "--json", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--report" in stderr or "--report" in stdout


def test_entries_detail_honours_the_output_flags_it_accepts(tmp_path: Path) -> None:
    """`--limit/--offset/--sort` are parsed for every `entries` form but only
    applied to the list — the detail and stats subpaths took them and did
    nothing, which reads as a working filter that silently is not one."""
    config_path, _bib = _library(
        tmp_path, "@article{a1,\n  title = {A},\n}\n@article{b2,\n  title = {B},\n}\n"
    )

    code, _stdout, stderr = _run(
        ["entries", "a1", "--limit", "1", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--limit" in stderr


def test_add_from_file_verbose_prints_the_diagnostics_it_promises(tmp_path: Path) -> None:
    """`--verbose` prints the metadata-selection diagnostics — on a single add.

    The batch path parsed the flag and never looked at it, so the one mode where
    per-item provider choices are hardest to follow was the one that would not
    explain them.
    """
    from pzi import capture_core

    config_path, _bib = _library(tmp_path)
    inputs = tmp_path / "inputs.txt"
    inputs.write_text("10.1145/3372297\n", encoding="utf-8")

    def _fake_capture(_input, _options, **_kwargs):
        return {
            "status": "ok", "action": "insert", "citekey": "a2024",
            "bib_name": "ml", "dry_run": False, "warnings": [], "errors": [],
            "metadata_diagnostics": ["selected result 1/2: score=41; crossref"],
        }

    original = capture_core.capture_to_bib
    capture_core.capture_to_bib = _fake_capture
    try:
        from pzi.commands import add as add_command

        original_cmd = add_command.capture_to_bib
        add_command.capture_to_bib = _fake_capture
        try:
            code, stdout, _stderr = _run(
                ["add", "--from-file", str(inputs), "--verbose",
                 "--config", str(config_path)],
                tmp_path,
            )
        finally:
            add_command.capture_to_bib = original_cmd
    finally:
        capture_core.capture_to_bib = original

    assert code == exit_codes.OK
    assert "selected result 1/2" in stdout


# ---------------------------------------------------------------------------
# The two commands that destroy a block, driven through their runners
# ---------------------------------------------------------------------------

_TWO_ENTRIES = (
    "@article{keep2019,\n  title = {Kept},\n  year = {2019},\n}\n\n"
    "@article{drop2020,\n  title = {Dropped},\n  year = {2020},\n}\n"
)


def test_delete_removes_the_entry_and_leaves_a_backup(tmp_path: Path) -> None:
    """`pzi delete` had no command-level test at all — the most destructive
    command in the CLI was covered only at the service layer, so the runner's
    own confirmation, backup reporting and exit code were unpinned."""
    config_path, bib = _library(tmp_path, _TWO_ENTRIES)

    code, stdout, stderr = _run(
        ["delete", "drop2020", "--force", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.OK
    written = bib.read_text(encoding="utf-8")
    assert "drop2020" not in written
    assert "keep2019" in written
    assert "backup saved to" in stderr
    assert "Dropped" in stdout or "deleted" in stdout
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _TWO_ENTRIES


def test_delete_without_force_refuses_when_stdin_is_not_a_terminal(tmp_path: Path) -> None:
    config_path, bib = _library(tmp_path, _TWO_ENTRIES)

    code, _stdout, stderr = _run(
        ["delete", "drop2020", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--force" in stderr
    assert bib.read_text(encoding="utf-8") == _TWO_ENTRIES


def test_delete_of_a_missing_citekey_is_not_found(tmp_path: Path) -> None:
    config_path, bib = _library(tmp_path, _TWO_ENTRIES)

    code, _stdout, _stderr = _run(
        ["delete", "nosuch2024", "--force", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.NOT_FOUND
    assert bib.read_text(encoding="utf-8") == _TWO_ENTRIES


def test_fix_merge_folds_one_entry_into_the_other(tmp_path: Path) -> None:
    """`pzi fix merge` had no command-level test either, and it destroys a block."""
    config_path, bib = _library(
        tmp_path,
        "@article{a2020,\n  title = {Same Paper},\n  year = {2020},\n"
        "  doi = {10.1000/same},\n  pages = {1--10},\n}\n\n"
        "@article{b2020,\n  title = {Same Paper},\n  year = {2020},\n"
        "  doi = {10.1000/same},\n}\n",
    )

    code, stdout, _stderr = _run(
        ["fix", "merge", "a2020", "b2020", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.OK
    written = bib.read_text(encoding="utf-8")
    assert "@article{a2020" not in written
    assert "@article{b2020" in written
    # The survivor takes over what only the dropped entry carried.
    assert "pages = {1--10}" in written
    assert "merged" in stdout
    assert list(tmp_path.glob("*.bak"))


def test_fix_merge_dry_run_writes_nothing(tmp_path: Path) -> None:
    before = (
        "@article{a2020,\n  title = {Same},\n  doi = {10.1000/same},\n}\n\n"
        "@article{b2020,\n  title = {Same},\n  doi = {10.1000/same},\n}\n"
    )
    config_path, bib = _library(tmp_path, before)

    code, stdout, _stderr = _run(
        ["fix", "merge", "a2020", "b2020", "--dry-run", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.OK
    assert bib.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.bak")) == []
    assert "would merge" in stdout


def test_fix_merge_of_a_missing_citekey_is_not_found(tmp_path: Path) -> None:
    config_path, bib = _library(tmp_path, _TWO_ENTRIES)

    code, _stdout, _stderr = _run(
        ["fix", "merge", "nosuch2024", "keep2019", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.NOT_FOUND
    assert bib.read_text(encoding="utf-8") == _TWO_ENTRIES


def test_export_force_without_an_output_path_is_refused(tmp_path: Path) -> None:
    """`--force` means "overwrite the file at -o", and there is no file."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")
    code, _out, err = _run(["export", "--config", str(config_path), "--force"], tmp_path)
    assert code == exit_codes.USAGE
    assert "--force applies to -o PATH" in err


def test_export_to_a_directory_says_it_is_a_directory(tmp_path: Path) -> None:
    """`Path.exists()` is true for a directory, so this said "file already exists".

    Which invites `--force` — and with `--force` it proceeded and died as a raw
    OSError naming the *temp* file, a path the user never typed.
    """
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")
    target_dir = tmp_path / "outdir"
    target_dir.mkdir()

    code, _out, err = _run(
        ["export", "--config", str(config_path), "-o", str(target_dir)], tmp_path
    )
    assert code == exit_codes.USAGE
    assert "is a directory" in err
    assert "already exists" not in err

    forced, _out2, err2 = _run(
        ["export", "--config", str(config_path), "-o", str(target_dir), "--force"], tmp_path
    )
    assert forced == exit_codes.USAGE
    assert ".tmp" not in err2


def test_reindex_force_and_dry_run_without_rename_are_refused(tmp_path: Path) -> None:
    """Both are accepted and do nothing: the run is already a read-only audit."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")
    for flag in ("--force", "--dry-run"):
        code, _out, err = _run(
            ["fix", "reindex", "--config", str(config_path), flag], tmp_path
        )
        assert code == exit_codes.USAGE, flag
        assert "already a read-only audit" in err, flag


def test_clean_dry_run_without_fix_is_refused(tmp_path: Path) -> None:
    """`--dry-run` reads as "I have made this safe" when it changed nothing."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")
    code, _out, err = _run(
        ["fix", "clean", "--config", str(config_path), "--dry-run"], tmp_path
    )
    assert code == exit_codes.USAGE
    assert "--dry-run previews --fix" in err


def test_repeated_target_keeps_every_library(tmp_path: Path) -> None:
    """`nargs="+"` with the default `store` action kept only the last one.

    On `search` that quietly halved the results. On `update`, which writes, the
    user asked for two libraries and got one.
    """
    import json

    first = tmp_path / "one.bib"
    first.write_text("@article{one2020,\n  title = {Findable One},\n}\n", encoding="utf-8")
    second = tmp_path / "two.bib"
    second.write_text("@article{two2020,\n  title = {Findable Two},\n}\n", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "one"\npath = "{first}"\ndefault = true\n\n'
        f'[[bibs]]\nname = "two"\npath = "{second}"\n',
        encoding="utf-8",
    )

    code, out, _err = _run(
        ["search", "--config", str(config_path), "--query", "Findable",
         "--target", "one", "--target", "two", "--json"],
        tmp_path,
    )
    assert code == exit_codes.OK
    citekeys = {item["citekey"] for item in json.loads(out)["items"]}
    assert citekeys == {"one2020", "two2020"}


def test_reindex_json_says_whether_it_applied_anything(tmp_path: Path) -> None:
    """A populated `changed[]` beside `backup_path: null` reads as "these happened"."""
    import json

    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n  year = {2020},\n}\n")
    code, out, _err = _run(["fix", "reindex", "--config", str(config_path), "--json"], tmp_path)
    assert code in (exit_codes.OK, exit_codes.FINDINGS)
    envelope = json.loads(out)
    assert envelope["applied"] is False
    assert envelope["bib_name"] == "ml"


def test_the_six_envelopes_that_never_named_their_library(tmp_path: Path) -> None:
    """`bib_name` was permanently null in six of eleven `--json` documents.

    README documents it as a real value, and the five other commands populate
    it, so a consumer keying on it silently got `None` for exactly these.
    """
    import json

    config_path, bib_path = _library(
        tmp_path, "@article{a1,\n  title = {A},\n  year = {2020},\n}\n"
    )
    source = tmp_path / "src.bib"
    source.write_text("@article{b2,\n  title = {B},\n  year = {2021},\n}\n", encoding="utf-8")

    invocations = [
        ["fix", "dedupe", "--config", str(config_path), "--json"],
        ["fix", "clean", "--config", str(config_path), "--json"],
        ["fix", "reindex", "--config", str(config_path), "--json"],
        ["fix", "merge", "a1", "a1", "--config", str(config_path), "--json"],
        ["delete", "nosuch", "--config", str(config_path), "--json", "--force"],
        ["import", str(source), "--config", str(config_path), "--json", "--dry-run"],
    ]
    for argv in invocations:
        _code, out, _err = _run(argv, tmp_path)
        envelope = json.loads(out)
        assert envelope["bib_name"] == "ml", argv


def test_an_unresolved_target_says_which_of_the_three_ways_it_failed(tmp_path: Path) -> None:
    """One string covered an unknown name, a missing .bib path, and no default.

    Those need three different actions from the user, and the config is loaded
    and in hand — so it can also name the libraries that would have worked.
    """
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    _code, _out, unknown = _run(
        ["entries", "--config", str(config_path), "--target", "nosuchlib"], tmp_path
    )
    assert "not a configured library" in unknown
    assert "configured: ml" in unknown

    _code2, _out2, missing_path = _run(
        ["entries", "--config", str(config_path), "--target", str(tmp_path / "gone.bib")],
        tmp_path,
    )
    assert "does not exist" in missing_path
    assert "configured libraries: ml" in missing_path

    no_default = tmp_path / "nodefault.toml"
    no_default.write_text(
        f'[[bibs]]\nname = "one"\npath = "{tmp_path / "one.bib"}"\n\n'
        f'[[bibs]]\nname = "two"\npath = "{tmp_path / "two.bib"}"\n',
        encoding="utf-8",
    )
    _code3, _out3, ambiguous = _run(["entries", "--config", str(no_default)], tmp_path)
    assert "no default library" in ambiguous
    assert "configured: one, two" in ambiguous


def test_check_report_dash_is_not_corrupted_by_the_human_table(tmp_path: Path) -> None:
    """`--jsonl -` was guarded against the table and `--report -` was not.

    Piping it into `jq` is the only reason `--report -` exists, and the
    appended plain-text table meant stdout was not JSON.
    """
    import json

    config_path, _bib = _library(tmp_path, "")
    code, out, _err = _run(["check", "--config", str(config_path), "--report", "-"], tmp_path)
    assert code in (exit_codes.OK, exit_codes.FINDINGS)
    assert json.loads(out)["status"] == "ok"


def test_check_will_not_clobber_an_existing_report_without_force(tmp_path: Path) -> None:
    """`export -o` refuses; these two truncated whatever was there.

    Worst on the long network-bound command: a bare `open(..., "w")` truncates
    up front, so an interrupted `check` destroyed the previous report and wrote
    nothing in its place.
    """
    config_path, _bib = _library(tmp_path, "")
    report = tmp_path / "audit.json"
    report.write_text('{"previous": "audit"}', encoding="utf-8")

    code, _out, err = _run(
        ["check", "--config", str(config_path), "--report", str(report)], tmp_path
    )
    assert code == exit_codes.USAGE
    assert "already exists" in err
    assert report.read_text(encoding="utf-8") == '{"previous": "audit"}'

    forced, _out2, _err2 = _run(
        ["check", "--config", str(config_path), "--report", str(report), "--force"], tmp_path
    )
    assert forced in (exit_codes.OK, exit_codes.FINDINGS)
    assert "previous" not in report.read_text(encoding="utf-8")
