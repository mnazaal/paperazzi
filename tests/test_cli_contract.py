"""Doing something other than what was typed is worse than refusing.

Every flag here was accepted and then ignored, and every preview here described
a write the real run does not perform.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_export_has_exactly_the_three_stdout_contracts_the_readme_documents(
    tmp_path: Path,
) -> None:
    """`export` is the one command whose stdout is a document, not a report.

    It therefore does *not* emit the `--json` envelope, and the README now says
    so case by case: a bare array for `--format json`, one prose line for
    `-o PATH`, and nothing at all on failure. Nothing checked any of it, which
    is how "the command whose native output is JSON is the one a JSON consumer
    cannot classify errors from" went unnoticed.
    """
    import json as _json

    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, stdout, _err = _run(
        ["export", "--format", "json", "--config", str(config_path)], tmp_path
    )
    assert code == exit_codes.OK
    parsed = _json.loads(stdout)
    assert isinstance(parsed, list), "a bare array, not the envelope"
    assert parsed[0]["citekey"] == "a1"

    out_path = tmp_path / "out.bib"
    code, stdout, _err = _run(
        ["export", "-o", str(out_path), "--config", str(config_path)], tmp_path
    )
    assert code == exit_codes.OK
    assert stdout.strip() == f"exported 1 entries to {out_path}"
    assert "@article{a1," in out_path.read_text(encoding="utf-8")

    # A failure writes no document at all — not a truncated one. The content is
    # rendered whole before anything is printed, which is what makes that true.
    #
    # Two failures, because they prove different things. A missing `--target`
    # fails in `resolve_target`, *before* any exporter runs, so `stdout == ""`
    # there is true for a reason that has nothing to do with the whole-then-
    # print property — a `print` on the real failure path left it green. The
    # second reaches `export_json`, which does the read itself, so the
    # exporter has genuinely started and still emitted nothing.
    code, stdout, stderr = _run(
        ["export", "--format", "json", "--target", str(tmp_path / "gone.bib"),
         "--config", str(config_path)],
        tmp_path,
    )
    assert code == exit_codes.ENVIRONMENT
    assert stdout == ""
    assert "does not exist" in stderr

    unreadable = tmp_path / "ml.bib"
    unreadable.chmod(0o000)
    try:
        code, stdout, stderr = _run(
            ["export", "--format", "json", "--config", str(config_path)], tmp_path
        )
    finally:
        unreadable.chmod(0o600)
    assert code == exit_codes.ENVIRONMENT
    assert stdout == "", f"a failed export wrote a partial document: {stdout!r}"
    assert "Permission denied" in stderr


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

    `delete` and `library merge` both confirm first; this one applied straight away,
    so a mistyped command was unrecoverable.
    """
    config_path, bib = _library(tmp_path, _RENAMEABLE)

    code, _stdout, stderr = _run(
        ["library", "reindex", "--rename-citekeys", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.USAGE
    assert "--force" in stderr
    assert bib.read_text(encoding="utf-8") == _RENAMEABLE


def test_reindex_rename_with_force_rewrites_and_leaves_a_backup(tmp_path: Path) -> None:
    config_path, bib = _library(tmp_path, _RENAMEABLE)

    code, _stdout, stderr = _run(
        ["library", "reindex", "--rename-citekeys", "--force", "--config", str(config_path)],
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
        ["library", "reindex", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.FINDINGS
    assert "--rename-citekeys" in stdout
    assert bib.read_text(encoding="utf-8") == _RENAMEABLE


def test_fix_clean_does_not_quarantine_a_sibling_librarys_pdf(tmp_path: Path) -> None:
    """The default layout points every configured bib at one `papers_dir`.

    `pzi library clean --fix` on one target moved the other library's PDFs into
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
        ["library", "clean", "--fix", "--target", "ml", "--config", str(config_path)],
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

    # The save/restore this used to do was a no-op — nothing reassigned the
    # attribute — so the test ran the *real* `ensure_translation_server`, which
    # clones three repositories. Patch it for real, and assert on the result
    # rather than on `status in {"ok", "error"}` and `code in {OK, ENVIRONMENT}`,
    # which together pin only "stdout parses as JSON".
    calls: list[object] = []

    def _refuse_to_install(*_args, **_kwargs):
        calls.append(_args)
        return None

    with patch.object(ts_backend, "ensure_translation_server", _refuse_to_install):
        code, stdout, _stderr = _run(
            ["doctor", "--reinstall-server", "--json", "--config", str(config_path)],
            tmp_path,
        )

    assert calls, "the runner never reached ensure_translation_server"
    envelope = json.loads(stdout)
    assert envelope["command"] == "doctor --reinstall-server"
    # The install was refused, so this is a failure and must say so on both
    # channels — the envelope and the exit code.
    assert envelope["status"] == "error"
    assert envelope["errors"]
    assert code == exit_codes.ENVIRONMENT
    # And it must not have touched what was already there.
    assert (ts_dir / "someone-elses-work.txt").exists()


def test_check_report_to_stdout_conflicts_with_json(tmp_path: Path) -> None:
    """Both write to stdout, so together they produce neither a valid report nor
    the single document `--json` promises. `--jsonl -` already had this guard."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")

    code, stdout, stderr = _run(
        ["library", "check", "--report", "-", "--json", "--config", str(config_path)], tmp_path
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
    """`pzi library merge` had no command-level test either, and it destroys a block."""
    config_path, bib = _library(
        tmp_path,
        "@article{a2020,\n  title = {Same Paper},\n  year = {2020},\n"
        "  doi = {10.1000/same},\n  pages = {1--10},\n}\n\n"
        "@article{b2020,\n  title = {Same Paper},\n  year = {2020},\n"
        "  doi = {10.1000/same},\n}\n",
    )

    code, stdout, _stderr = _run(
        ["library", "merge", "a2020", "b2020", "--config", str(config_path)], tmp_path
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
        ["library", "merge", "a2020", "b2020", "--dry-run", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.OK
    assert bib.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("*.bak")) == []
    assert "would merge" in stdout


def test_fix_merge_of_a_missing_citekey_is_not_found(tmp_path: Path) -> None:
    config_path, bib = _library(tmp_path, _TWO_ENTRIES)

    code, _stdout, _stderr = _run(
        ["library", "merge", "nosuch2024", "keep2019", "--config", str(config_path)],
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
            ["library", "reindex", "--config", str(config_path), flag], tmp_path
        )
        assert code == exit_codes.USAGE, flag
        assert "already a read-only audit" in err, flag


def test_clean_dry_run_without_fix_is_refused(tmp_path: Path) -> None:
    """`--dry-run` reads as "I have made this safe" when it changed nothing."""
    config_path, _bib = _library(tmp_path, "@article{a1,\n  title = {A},\n}\n")
    code, _out, err = _run(
        ["library", "clean", "--config", str(config_path), "--dry-run"], tmp_path
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
    code, out, _err = _run(["library", "reindex", "--config", str(config_path), "--json"], tmp_path)
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
        ["library", "dedupe", "--config", str(config_path), "--json"],
        ["library", "clean", "--config", str(config_path), "--json"],
        ["library", "reindex", "--config", str(config_path), "--json"],
        ["library", "merge", "a1", "a1", "--config", str(config_path), "--json"],
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
    assert "none is marked default" in ambiguous
    assert "configured: one, two" in ambiguous

    # None of the three names a CLI flag. Three front ends read these strings,
    # and `pzi.export(library="nope")` used to raise "`--target` 'nope' is not a
    # configured library" — naming a flag the caller never typed and cannot
    # pass. The failure is the same everywhere; only the spelling differs, so
    # the message describes the thing and leaves the spelling to the reader.
    for text in (unknown, missing_path, ambiguous):
        assert "--target" not in text


def test_check_report_dash_is_not_corrupted_by_the_human_table(tmp_path: Path) -> None:
    """`--jsonl -` was guarded against the table and `--report -` was not.

    Piping it into `jq` is the only reason `--report -` exists, and the
    appended plain-text table meant stdout was not JSON.
    """
    import json

    config_path, _bib = _library(tmp_path, "")
    code, out, _err = _run(["library", "check", "--config", str(config_path), "--report", "-"], tmp_path)
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
        ["library", "check", "--config", str(config_path), "--report", str(report)], tmp_path
    )
    assert code == exit_codes.USAGE
    assert "already exists" in err
    assert report.read_text(encoding="utf-8") == '{"previous": "audit"}'

    forced, _out2, _err2 = _run(
        ["library", "check", "--config", str(config_path), "--report", str(report), "--force"], tmp_path
    )
    assert forced in (exit_codes.OK, exit_codes.FINDINGS)
    assert "previous" not in report.read_text(encoding="utf-8")


def test_reindex_says_which_scheme_it_would_use(tmp_path: Path) -> None:
    """With no `citekey_format`, `--rename-citekeys` rewrites every key to
    pzi's built-in scheme. For a library imported from Zotero that is every key
    the user's .tex files cite, and the prompt did not mention it."""
    config_path, _bib = _library(tmp_path, "@article{Smith_2024_Deep,\n  title = {A},\n}\n")

    # stdin is not a tty under pytest, so this takes the refusal path — which is
    # the one that cannot prompt, and therefore most needs to say it.
    code, _out, err = _run(
        ["library", "reindex", "--rename-citekeys", "--config", str(config_path)], tmp_path
    )
    assert code == exit_codes.USAGE
    assert "no citekey_format is configured" in err


def test_a_bare_subcommand_group_prints_its_own_help(tmp_path: Path) -> None:
    """`pzi library` said `error: the following arguments are required: library_command`.

    That names an internal argparse dest, appears in no documentation, and
    tells the user nothing about what the group contains. The group's help
    lists exactly the subcommands they were reaching for.

    Still exit 2 and still stderr: a bare group is an incomplete invocation, so
    `pzi library && deploy` must not run `deploy` having done nothing, and stdout
    stays clean for whatever is being piped.
    """
    expected = {
        "library": ("list", "check", "clean", "dedupe", "merge", "reindex"),
        "tag": ("add", "remove", "list"),
        "pdf": ("retry", "attach"),
    }
    for group, subcommands in expected.items():
        code, out, err = _run([group], tmp_path)
        assert code == exit_codes.USAGE, group
        assert out == "", f"{group} wrote to stdout"
        assert f"usage: pzi {group}" in err, group
        # The choices line argparse builds from the registry, not a bare
        # substring. Every one of these names also appears in the group's
        # description and epilog prose — `library`'s description spells out all
        # six — so `assert sub in err` stayed true after renaming a subparser,
        # which is the one thing this assertion is for.
        assert "{" + ",".join(subcommands) + "}" in err, (
            f"{group}: expected the choices line for {subcommands}, got:\n{err}"
        )
        assert f"{group}_command" not in err, f"{group} still names the internal dest"


def test_the_reindex_prompt_names_the_scheme_it_would_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interactive path — the one a person actually answers.

    The non-tty refusal is covered above; this drives the prompt itself, which
    is where someone decides whether to rewrite every citekey in their library.
    """
    import io

    config_path, _bib = _library(tmp_path, "@article{Smith_2024,\n  title = {A},\n}\n")

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"), raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    code, _out, err = _run(
        ["library", "reindex", "--rename-citekeys", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.OK  # answered "n"
    assert "Rewrite every citekey" in err
    assert "using the built-in scheme" in err
    assert "no citekey_format is configured" in err
    assert "cancelled" in err


# --- Remediation after the 2026-08-15 API review -----------------------------

ARTICLE = "@article{a1,\n  title = {An Article},\n}\n"


def test_no_command_reads_a_bare_second_target_as_a_second_library(
    tmp_path: Path,
) -> None:
    """`--target a b` must not mean two different things on two commands.

    `search` and `update` declared `nargs="+"`, so `--target a b` named two
    libraries; the other twelve declared a scalar, so argparse handed `b` to the
    command's *positional* — `pzi entries --target main main` reported "no entry
    with citekey main". Same spelling, opposite meanings, and no error either
    way.

    The greedy form is gone rather than made uniform: `nargs="+"` also swallows
    a command's own positional, so `pzi add --target lib 10.1234/x` would lose
    the DOI. Several libraries are now spelled by repeating the flag.
    """
    config_path, _ = _library(tmp_path, ARTICLE)

    # On a command with no positional, the second bare value is simply rejected.
    code, _out, err = _run(
        ["search", "--query", "Article", "--target", "ml", "ml",
         "--config", str(config_path)],
        tmp_path,
    )
    assert code == exit_codes.USAGE
    assert "unrecognized arguments: ml" in err, err

    # Repeating the flag is the one spelling that names several libraries.
    code, _out, _err = _run(
        ["search", "--query", "Article", "--target", "ml", "--target", "ml",
         "--config", str(config_path)],
        tmp_path,
    )
    assert code == exit_codes.OK

    # And a command's own positional still reaches it — the regression the
    # greedy form would have introduced.
    code, _out, err = _run(
        ["entries", "a1", "--target", "ml", "--config", str(config_path)], tmp_path
    )
    assert code == exit_codes.OK, err


def test_check_force_without_a_report_destination_is_refused(tmp_path: Path) -> None:
    """A flag that is accepted and ignored reads as applied.

    `--force` on `check` means "overwrite the file at --report/--jsonl"; with
    neither given it was consulted only inside a loop that never ran. This CLI
    refuses the same pattern in six other places, and `check` is the longest
    network-bound command — the worst one to discover a no-op flag on.
    """
    config_path, _ = _library(tmp_path, ARTICLE)
    code, _out, err = _run(["library", "check", "--force", "--config", str(config_path)], tmp_path)
    assert code == exit_codes.USAGE
    assert "--force applies to --report/--jsonl" in err, err


def test_every_json_failure_carries_a_reason(tmp_path: Path) -> None:
    """`reason` present on some failures and absent on others is the worst case.

    A consumer writes the branch against the failures that have it, then meets
    one that does not. The same user error reported `config` through the service
    path and nothing at all through the boundary path.
    """
    import json

    config_path, _ = _library(tmp_path, ARTICLE)
    missing = str(tmp_path / "nope.bib")

    for argv in (
        ["entries", "--target", missing],
        ["entries", "--stats", "--target", missing],
        ["search", "--query", "x", "--target", missing],
    ):
        code, out, _err = _run([*argv, "--json", "--config", str(config_path)], tmp_path)
        envelope = json.loads(out)
        assert envelope["status"] == "error"
        assert envelope.get("reason") == "config", (argv, envelope)
        assert code == exit_codes.ENVIRONMENT


def test_library_list_aligns_its_columns_and_marks_the_default(
    tmp_path: Path,
) -> None:
    """The human-readable path of `pzi library list`, which nothing ran.

    Every invocation in the suite went through `--json`, a parser snapshot or
    an identity assertion, so the column padding and the `(default)` marker —
    the entire reason this command exists rather than `pzi doctor` — were
    executed by no test. The module sat at 44% while looking covered.
    """
    long_bib = tmp_path / "with-a-long-name.bib"
    short_bib = tmp_path / "ml.bib"
    for path in (long_bib, short_bib):
        path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{short_bib}"\ndefault = true\n'
        f'\n[[bibs]]\nname = "a-much-longer-name"\npath = "{long_bib}"\n',
        encoding="utf-8",
    )

    code, stdout, stderr = _run(["library", "list", "--config", str(config_path)], tmp_path)

    assert code == exit_codes.OK, stderr
    lines = stdout.splitlines()
    assert len(lines) == 2, stdout
    # Padded to the widest name, so the paths line up in a terminal.
    assert [line.index(str(tmp_path)) for line in lines] == [
        len("a-much-longer-name") + 2
    ] * 2, stdout
    # Exactly one default, and it is the one the config marked.
    assert [line for line in lines if line.endswith("  (default)")] == [lines[0]]
    assert lines[0].startswith("ml ")


def test_export_says_a_missing_target_once(tmp_path: Path) -> None:
    """The symptom `distinct_details` exists for, on the path that produced it.

    `commands/common.py` raises `PziError(resolved.errors[0],
    details=list(resolved.errors))`, so `message` and `details[0]` are the same
    string, and `cli._fail` printed the headline and then bulleted it — one
    failure reported as two.

    `error_lines` had a unit test naming this exact invocation, but
    `error_lines` is the *other* renderer sharing the rule; `_fail` had none.
    Reverting `_fail`'s `distinct_details` call left the whole suite green.
    Counted rather than substring-matched, because "does not exist" appearing
    somewhere in stderr is true either way.
    """
    config_path, _bib = _library(tmp_path)

    code, stdout, stderr = _run(
        ["export", "--target", str(tmp_path / "gone.bib"), "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.ENVIRONMENT, stderr
    assert stdout == ""
    assert stderr.count("does not exist") == 1, stderr
    assert stderr.count("\n") == 1, f"one line, not a headline plus a bullet: {stderr!r}"

