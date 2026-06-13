from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi.commands.update import run_update_command


def test_run_update_command_calls_service_for_each_target(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_update_bib(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": kwargs["bib_selector"] or "main",
            "dry_run": kwargs["dry_run"],
            "items": [],
            "warnings": [],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(target=["main", "ml"], dry_run=True, verbose=False)

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        update_bib_fn=fake_update_bib,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "main",
            "dry_run": True,
        },
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
            "dry_run": True,
        },
    ]
    assert stdout.getvalue() == "DRY RUN: no updates\nDRY RUN: no updates\n"
    assert stderr.getvalue() == ""


def test_run_update_command_prints_diffs_and_diagnostics(tmp_path: Path) -> None:
    def fake_update_bib(**kwargs):
        return {
            "status": "ok",
            "bib_name": "main",
            "dry_run": True,
            "items": [
                {
                    "citekey": "smith2024graph",
                    "changed_fields": ["doi"],
                    "note": "crossref",
                    "diff": "--- old\n+++ new\n",
                    "metadata_diagnostics": ["doi: found"],
                }
            ],
            "warnings": [],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(target=None, dry_run=True, verbose=True)

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        update_bib_fn=fake_update_bib,
    )

    assert exit_code == 0
    assert "DRY RUN: smith2024graph: doi [crossref]" in stdout.getvalue()
    assert "--- old\n+++ new\n" in stdout.getvalue()
    assert "metadata diagnostics:\n  doi: found\n" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_run_update_command_surfaces_metadata_warnings_without_verbose(tmp_path: Path) -> None:
    """Low-confidence advisories on items must reach stderr even without --verbose."""
    def fake_update_bib(**kwargs):
        return {
            "status": "ok",
            "bib_name": "main",
            "dry_run": False,
            "items": [
                {
                    "citekey": "smith2024graph",
                    "changed_fields": ["doi"],
                    "note": None,
                    "metadata_warnings": [
                        "metadata confidence low: candidate score=1 below 2; verify"
                    ],
                }
            ],
            "warnings": [],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(target=None, dry_run=False, verbose=False)

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        update_bib_fn=fake_update_bib,
    )

    assert exit_code == 0
    assert "warning: metadata confidence low" in stderr.getvalue()


def test_run_update_command_returns_failure_when_any_target_fails(tmp_path: Path) -> None:
    def fake_update_bib(**kwargs):
        if kwargs["bib_selector"] == "bad":
            return {"status": "error", "errors": ["missing bib"]}
        return {
            "status": "ok",
            "bib_name": kwargs["bib_selector"],
            "dry_run": False,
            "items": [],
            "warnings": [],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(target=["good", "bad"], dry_run=False, verbose=False)

    exit_code = run_update_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        update_bib_fn=fake_update_bib,
    )

    assert exit_code == 1
    assert stdout.getvalue() == "no updates\n"
    assert stderr.getvalue() == "update failed\n- missing bib\n"
