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
