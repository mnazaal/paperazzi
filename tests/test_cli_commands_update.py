from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import exit_codes
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

    assert exit_code == exit_codes.ENVIRONMENT
    assert stdout.getvalue() == "no updates\n"
    assert stderr.getvalue() == "update failed (bad)\n- missing bib\n"


def test_update_exits_partial_when_a_record_failed(tmp_path: Path) -> None:
    """A run where records failed exited 0 — failures lived only in free text.

    `note` could not serve as the predicate (it is set for benign outcomes too)
    and neither could `applied`, which is False for every item of a healthy
    dry-run. The service now marks failures structurally.
    """
    def fake_update_bib(**_kwargs):
        return {
            "status": "ok",
            "bib_name": "ml",
            "dry_run": False,
            "items": [
                {"citekey": "ok2024", "changed_fields": ["doi"],
                 "applied": True, "note": None},
                {"citekey": "bad2024", "changed_fields": [],
                 "applied": False, "note": "update failed: boom", "failed": True},
            ],
            "errors": [],
        }

    exit_code = run_update_command(
        Namespace(target=["ml"], dry_run=False, verbose=False),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        update_bib_fn=fake_update_bib,
    )

    assert exit_code == exit_codes.PARTIAL


def test_update_dry_run_with_no_failures_still_exits_ok(tmp_path: Path) -> None:
    """Guards the trap: every item of a healthy dry-run has `applied is False`."""
    def fake_update_bib(**_kwargs):
        return {
            "status": "ok",
            "bib_name": "ml",
            "dry_run": True,
            "items": [
                {"citekey": "a2024", "changed_fields": ["doi"],
                 "applied": False, "note": None},
                {"citekey": "b2024", "changed_fields": ["year"],
                 "applied": False, "note": None},
            ],
            "errors": [],
        }

    exit_code = run_update_command(
        Namespace(target=["ml"], dry_run=True, verbose=False),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        update_bib_fn=fake_update_bib,
    )

    assert exit_code == exit_codes.OK
