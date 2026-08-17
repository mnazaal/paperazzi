"""Exit-code contract for `pzi library dedupe`."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pzi.commands import dedupe as dedupe_command


def _result(*, exact: int = 0, fuzzy: int = 0) -> dict[str, Any]:
    return {
        "status": "ok",
        "bib_path": "/tmp/lib.bib",
        "total_entries": 2,
        "exact_duplicates": [{"citekeys": ["a", "b"]}] * exact,
        "fuzzy_candidates": [{"citekey": "a", "hint": "b"}] * fuzzy,
        "total_clusters": exact,
        "errors": [],
    }


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    result: dict[str, Any],
    *,
    json_output: bool = False,
) -> tuple[int, str]:
    monkeypatch.setattr(
        dedupe_command, "resolve_target", lambda **_kw: ({}, {"name": "ml", "path": "/tmp/lib.bib"})
    )
    monkeypatch.setattr(dedupe_command, "find_duplicates", lambda **_kw: result)
    stdout, stderr = StringIO(), StringIO()
    code = dedupe_command.run_dedupe_command(
        Namespace(config=None, target=None, json=json_output),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
    )
    return code, stdout.getvalue()


def test_dedupe_exits_zero_when_nothing_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _out = _run(monkeypatch, tmp_path, _result())
    assert code == 0


def test_dedupe_exits_one_on_exact_duplicates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _out = _run(monkeypatch, tmp_path, _result(exact=1))
    assert code == 1


def test_dedupe_exits_one_on_fuzzy_candidates_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 1 means "ran fine, has something to report".

    ``total_clusters`` counts only exact clusters, so keying the exit code off
    it alone reported success on a library whose sole finding was fuzzy.
    """
    code, out = _run(monkeypatch, tmp_path, _result(fuzzy=1))
    assert code == 1
    assert "fuzzy" in out


def test_dedupe_exits_one_on_fuzzy_candidates_alone_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _out = _run(monkeypatch, tmp_path, _result(fuzzy=1), json_output=True)
    assert code == 1


def test_fix_merge_unknown_citekey_is_not_found(tmp_path: Path) -> None:
    """Both halves were missing here: the service `reason` and the runner branch."""
    from pzi.dedupe_service import merge_duplicates

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{real2024, title = {Real}}\n")

    result = merge_duplicates(
        bib_path=str(bib_path), citekey_a="nosuch2024", citekey_b="real2024",
    )

    assert result["status"] == "error"
    assert result["reason"] == "not_found"


def test_fix_merge_self_merge_is_not_tagged_not_found(tmp_path: Path) -> None:
    """"Cannot merge an entry with itself" is a usage mistake, not a missing entry."""
    from pzi import exit_codes
    from pzi.dedupe_service import merge_duplicates
    from pzi.errors import exit_code_for_error

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{real2024, title = {Real}}\n")

    result = merge_duplicates(
        bib_path=str(bib_path), citekey_a="real2024", citekey_b="real2024",
    )

    assert result["status"] == "error"
    assert result["reason"] == "usage"
    # And therefore exit 2, not 5. `exit_code_for_error` sent every failure
    # without `reason == "not_found"` to ENVIRONMENT, so retyping a mistake the
    # user made was reported as "this machine cannot run the command".
    assert exit_code_for_error(result) == exit_codes.USAGE


def test_fix_merge_names_the_survivor_fields_it_overwrites(tmp_path: Path) -> None:
    """The dry run is where the user decides, and it never mentioned the loss.

    `merge_entries` prefers the longer string for title, venue and abstract, so
    the dropped entry's value replaces the survivor's. The runner printed
    `carried_fields` and `dropped_fields` — the latter computed only over
    identical raw field keys — but never `changed_fields`, which is exactly the
    set of survivor fields the merge overwrites. In text mode, dry run and real
    run alike, the loss was never mentioned at all.
    """
    from pzi.commands import dedupe as dedupe_command

    bib = tmp_path / "lib.bib"
    bib.write_text(
        "@article{keep2020,\n"
        "  title = {Short Title},\n"
        "  author = {A, B},\n"
        "  year = {2020},\n"
        "}\n\n"
        "@article{drop2020,\n"
        "  title = {A Considerably Longer Title That Will Win},\n"
        "  author = {A, B},\n"
        "  year = {2020},\n"
        "}\n"
    )
    config = tmp_path / "config.toml"
    config.write_text(
        'translation_server_url = "http://127.0.0.1:59999"\n\n'
        f'[[bibs]]\nname = "main"\npath = "{bib}"\ndefault = true\n'
    )

    out, err = StringIO(), StringIO()
    args = Namespace(
        citekey_a="drop2020", citekey_b="keep2020", dry_run=True, json=False, target=None
    )
    dedupe_command.run_merge_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(config),
        stdout=out,
        stderr=err,
        bib_selector=None,
    )

    text = out.getvalue()
    assert "overwritten by drop2020: title" in text, text
    # And it must no longer claim the opposite.
    assert "fields kept from keep2020 (conflict): title" not in text, text

    # "In dry run and real run alike" — this test said that and then checked
    # only the dry run, so the applied path went on setting `overwritten_fields`
    # nowhere at all: the run that destroys the field was the silent one.
    out_real, err_real = StringIO(), StringIO()
    dedupe_command.run_merge_command(
        Namespace(
            citekey_a="drop2020", citekey_b="keep2020",
            dry_run=False, json=False, target=None,
        ),
        home_dir=str(tmp_path),
        config_path=str(config),
        stdout=out_real,
        stderr=err_real,
        bib_selector=None,
    )

    real_text = out_real.getvalue()
    assert "overwritten by drop2020: title" in real_text, real_text
    # And the file agrees: the survivor's title really was replaced.
    assert "A Considerably Longer Title That Will Win" in bib.read_text()
