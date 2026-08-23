"""`pzi import`'s human-readable output, which no test executed.

The module sat at 53%: every import test went through `--json` or through
`import_service` directly, so the whole text-mode block — the DRY RUN prefix,
the per-entry ✓/✗ marks, the error and warning lines — ran nowhere. Two of
those renderings had already been wrong once (a cross beside every entry an
import successfully *updated*, and the writer's near-duplicate warning printed
nowhere at all), which is what a 53% module with a contract looks like.
"""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path

import pytest

from pzi import exit_codes
from pzi.commands import import_ as import_command


def _args(**kw) -> Namespace:
    base = dict(source="source.bib", json=False, dry_run=False, force_new=False,
                target=None, config=None)
    base.update(kw)
    return Namespace(**base)


def _run(args: Namespace, result, tmp_path: Path,
         monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    monkeypatch.setattr(import_command, "import_from_bibtex", lambda **_kw: result)
    stdout, stderr = StringIO(), StringIO()
    code = import_command.run_import_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _result(**kw):
    base = {
        "status": "ok",
        "bib_name": "ml",
        "total_source": 3,
        "imported": 1,
        "updated": 1,
        "skipped_duplicates": 1,
        "skipped_errors": 0,
        "results": [
            {"citekey": "new2024", "status": "imported"},
            {"citekey": "old2020", "status": "updated"},
            {"citekey": "dup2019", "status": "duplicate"},
        ],
        "errors": [],
        "warnings": [],
    }
    base.update(kw)
    return base


def test_text_mode_reports_every_counter_and_marks_only_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An update and a correctly-skipped duplicate are successes, not failures.

    The per-entry mark used to be ✗ for anything that was not an insert, so a
    run that updated ten entries exactly as asked showed ten crosses.
    """
    code, out, err = _run(_args(), _result(), tmp_path, monkeypatch)

    assert code == exit_codes.OK, err
    assert "imported 1/3 entries" in out
    assert "updated 1 existing entries" in out
    assert "skipped 1 duplicates" in out
    assert "✗" not in out
    assert out.count("✓") == 3


def test_dry_run_prefixes_every_count_it_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, _err = _run(
        _args(dry_run=True), _result(), tmp_path, monkeypatch
    )

    assert "DRY RUN: imported 1/3 entries" in out
    assert "DRY RUN: updated 1 existing entries" in out
    assert "DRY RUN: skipped 1 duplicates" in out


def test_an_erroring_entry_is_marked_counted_and_exits_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad entry among good ones is the batch contract's PARTIAL case.

    The errors and the writer's warnings both go to stderr, so a piped stdout
    still holds only the counts — and the near-duplicate warning is the one that
    matters, since silently doubling the library is what `import` must not do.
    """
    result = _result(
        imported=1,
        updated=0,
        skipped_duplicates=0,
        skipped_errors=1,
        total_source=2,
        results=[
            {"citekey": "good2024", "status": "imported"},
            {"citekey": "bad2024", "status": "error"},
        ],
        errors=["bad2024: no title"],
        warnings=["good2024 looks like a near-duplicate of good2023"],
    )

    code, out, err = _run(_args(), result, tmp_path, monkeypatch)

    assert code == exit_codes.PARTIAL, err
    assert "1 errors" in out
    assert "  ✓ good2024: imported" in out
    assert "  ✗ bad2024: error" in out
    assert "! bad2024: no title" in err
    assert "near-duplicate" in err


def test_a_failed_import_exits_the_code_its_reason_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service classifies; the runner does not re-decide.

    `import_service` calls an unreadable source `usage`, and hardcoding 5 here
    made the envelope say "retype" while the exit status said "retry".
    """
    result = {
        "status": "error",
        "message": "source file not found",
        "reason": "usage",
        "errors": ["file not found: nope.bib"],
    }

    code, out, err = _run(_args(), result, tmp_path, monkeypatch)

    assert code == exit_codes.USAGE
    assert out == ""
    assert "import failed" in err
    assert "file not found: nope.bib" in err
