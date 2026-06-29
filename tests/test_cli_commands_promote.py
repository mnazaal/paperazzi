from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi.commands.update import run_update_command


def run_promote_command(args, **kwargs):
    """Adapter: `pzi promote` folded into `pzi update --promote`."""
    args.promote = True
    return run_update_command(args, **kwargs)


def test_run_promote_command_calls_service_for_each_target(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_promote_bib(**kwargs):
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
    args = Namespace(target=["main", "ml"], dry_run=True, replace=True, verbose=False)

    exit_code = run_promote_command(
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
            "bib_selector": "main",
            "dry_run": True,
            "keep_preprint": False,
            "mark_resolved": False,
        },
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
            "dry_run": True,
            "keep_preprint": False,
            "mark_resolved": False,
        },
    ]
    assert "DRY RUN: no preprints to promote" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_run_promote_command_prints_diffs_and_diagnostics(tmp_path: Path) -> None:
    def fake_promote_bib(**kwargs):
        return {
            "status": "ok",
            "bib_name": "main",
            "dry_run": True,
            "items": [
                {
                    "action": "create",
                    "preprint_citekey": "smith2024graph",
                    "published_citekey": "smith2024published",
                    "changed_fields": ["doi", "journal"],
                    "note": "published version found",
                    "pdf_attached": True,
                    "diff": "--- old\n+++ new\n",
                    "metadata_diagnostics": ["doi: found"],
                }
            ],
            "warnings": [],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(target=None, dry_run=True, replace=False, verbose=True)

    exit_code = run_promote_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        promote_bib_fn=fake_promote_bib,
    )

    assert exit_code == 0
    assert "--- old\n+++ new\n" in stdout.getvalue()
    assert "metadata diagnostics:\n  doi: found\n" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_run_promote_command_returns_failure_when_any_target_fails(tmp_path: Path) -> None:
    def fake_promote_bib(**kwargs):
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
    args = Namespace(target=["good", "bad"], dry_run=False, replace=False, verbose=False)

    exit_code = run_promote_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        promote_bib_fn=fake_promote_bib,
    )

    assert exit_code == 1
    assert "no preprints to promote" in stdout.getvalue()
    assert stderr.getvalue() == "promote failed\n- missing bib\n"
