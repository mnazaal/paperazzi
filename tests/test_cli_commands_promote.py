from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import exit_codes
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
    # `on_item` is a closure, so it is checked separately; the rest is pinned
    # exactly, so a new kwarg on the service still fails this test.
    assert all(callable(call.pop("on_item")) for call in calls)
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "main",
            "dry_run": True,
            "keep_preprint": False,
            "mark_resolved": False,
            "limit": None,
        },
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
            "dry_run": True,
            "keep_preprint": False,
            "mark_resolved": False,
            "limit": None,
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

    assert exit_code == exit_codes.ENVIRONMENT
    assert "no preprints to promote" in stdout.getvalue()
    assert stderr.getvalue() == "promote failed (bad)\n- missing bib\n"


# ── Progress: a sweep that prints nothing looks like a hang ─────────────


def _promote_args(**over):
    from argparse import Namespace
    base = dict(target=None, dry_run=True, replace=False, verbose=False,
                promote=True, mark_resolved=False, limit=12, json=False)
    base.update(over)
    return Namespace(**base)


def _stub_result(n: int):
    from pzi.promote_service import _empty_summary

    return {
        "status": "ok", "bib_name": "main", "dry_run": True, "warnings": [], "errors": [],
        "items": [
            {"preprint_citekey": f"pre{i}", "note": "no published candidate found",
             "action": "skip", "failed": False, "changed_fields": [],
             "published_citekey": None, "pdf_attached": False}
            for i in range(n)
        ],
        # Built from the service's own empty summary rather than a literal, so
        # this stub cannot drift out of the shape the renderer expects.
        "summary": {**_empty_summary(), "checked": n, "eligible": n + 4, "remaining": 4},
    }


def test_each_candidate_is_reported_as_it_is_decided(tmp_path: Path) -> None:
    """One line per candidate, because each costs seconds.

    `check` batches progress every 25 entries; it audits one in well under a
    second. `promote` waits out the providers' polite intervals at ~6 s each, so
    batching would leave a ten-minute run silent for its first two minutes —
    which is what the first version of this did.
    """
    from io import StringIO

    from pzi.commands.update import run_update_command

    def fake_promote_bib(**kwargs):
        on_item = kwargs.get("on_item")
        result = _stub_result(12)
        for index, item in enumerate(result["items"], start=1):
            if on_item is not None:
                on_item(item, index, 12)
        return result

    config = tmp_path / "config.toml"
    config.write_text('[[bibs]]\nname = "main"\npath = "x.bib"\ndefault = true\n')
    stderr = StringIO()
    run_update_command(
        _promote_args(), home_dir=str(tmp_path), config_path=str(config),
        stdout=StringIO(), stderr=stderr, promote_bib_fn=fake_promote_bib,
    )
    lines = stderr.getvalue().splitlines()
    assert "[1/12] pre0" in lines[0], lines
    assert "[12/12] pre11" in lines[11]
    assert any("4 preprints not checked" in line for line in lines)


def test_json_mode_streams_nothing_to_stderr(tmp_path: Path) -> None:
    """`--json` promises one document; progress chatter is not part of it."""
    from io import StringIO

    from pzi.commands.update import run_update_command

    captured: dict = {}

    def fake_promote_bib(**kwargs):
        captured["on_item"] = kwargs.get("on_item")
        return _stub_result(2)

    config = tmp_path / "config.toml"
    config.write_text('[[bibs]]\nname = "main"\npath = "x.bib"\ndefault = true\n')
    stderr = StringIO()
    run_update_command(
        _promote_args(json=True), home_dir=str(tmp_path), config_path=str(config),
        stdout=StringIO(), stderr=stderr, promote_bib_fn=fake_promote_bib,
    )
    assert captured["on_item"] is None
    assert stderr.getvalue() == ""
