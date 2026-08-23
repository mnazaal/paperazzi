"""`library clean`'s verdict on a library it could only partly read.

Until 2026-08-23 an unparseable block made `clean` exit 5 and print
`clean failed` — a run that happened, reported as a run that could not happen,
with the reason suppressed in text mode while `--json` was told the line number.

The verdict is now 1: the audit ran, and what it found is that part of the file
is unreadable. What the output must *not* do is imply the rest is healthy —
`validate_library` returns early on a parse failure, so the empty `orphan_pdfs`
and `missing_pdfs` mean **not checked**, not "none found".
"""

from __future__ import annotations

import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import exit_codes
from pzi.commands.clean import run_clean_command


def _library(tmp_path: Path, bib_text: str, *, orphans: int = 0) -> tuple[str, str]:
    papers = tmp_path / "papers"
    papers.mkdir()
    for i in range(orphans):
        (papers / f"orphan{i}.pdf").write_text("not a real pdf")
    bib = tmp_path / "main.bib"
    bib.write_text(bib_text)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "t"\npath = "{bib}"\npapers_dir = "{papers}"\ndefault = true\n'
    )
    return str(config), str(bib)


_PARTLY_READABLE = "@article{good2020,\n  title = {Fine},\n}\n\n@article{broken,\n  title = {x\n"


def _run(config_path: str, tmp_path: Path, *, as_json: bool = False) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    code = run_clean_command(
        Namespace(fix=False, dry_run=False, json=as_json, target=None, config=config_path),
        home_dir=str(tmp_path),
        config_path=config_path,
        stdout=out,
        stderr=err,
        bib_selector=None,
    )
    return code, out.getvalue(), err.getvalue()


def test_a_partly_read_library_is_a_finding_not_a_failure_to_run(tmp_path: Path) -> None:
    config, _ = _library(tmp_path, _PARTLY_READABLE)
    code, _out, err = _run(config, tmp_path)
    assert code == exit_codes.FINDINGS
    # The reason, which text mode used to swallow entirely.
    assert "unparseable BibTeX block at line 5" in err


def test_the_text_output_says_the_remaining_checks_did_not_run(tmp_path: Path) -> None:
    """The empty lists mean "not checked"; silence about that reads as health."""
    config, _ = _library(tmp_path, _PARTLY_READABLE, orphans=2)
    _code, _out, err = _run(config, tmp_path)
    assert "did not run" in err


def test_both_output_modes_reach_the_same_verdict(tmp_path: Path) -> None:
    """One invocation must not tell a script and a human different things."""
    config, _ = _library(tmp_path, _PARTLY_READABLE)
    text_code, _o1, _e1 = _run(config, tmp_path)
    json_code, out, _e2 = _run(config, tmp_path, as_json=True)
    assert text_code == json_code == exit_codes.FINDINGS
    payload = json.loads(out)
    assert payload["partial_parse"] is True
    assert payload["errors"]


def test_the_empty_pdf_lists_on_a_partial_read_are_not_evidence_of_health(
    tmp_path: Path,
) -> None:
    """Two orphans exist; a partial read reports none, because it never looked.

    Pinned so nobody later "fixes" the empty lists by computing them: a dropped
    entry contributes no referenced path, so its PDF looks orphaned and `--fix`
    would quarantine it.
    """
    config, _ = _library(tmp_path, _PARTLY_READABLE, orphans=2)
    _code, out, _err = _run(config, tmp_path, as_json=True)
    payload = json.loads(out)
    assert payload["orphan_pdfs"] == []
    assert payload["partial_parse"] is True


def test_a_healthy_library_still_exits_zero(tmp_path: Path) -> None:
    config, _ = _library(tmp_path, "@article{a2020,\n  title = {A},\n}\n")
    code, _out, _err = _run(config, tmp_path)
    assert code == exit_codes.OK
