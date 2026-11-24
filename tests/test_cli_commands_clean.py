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


def test_clean_dry_run_help_says_it_needs_fix(capsys) -> None:
    """`--dry-run` is refused without `--fix`, but the help said "report issues
    without fixing", which is what a plain run already does."""
    import pytest

    from pzi.cli_parser import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["library", "clean", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "with --fix, preview" in help_text


def test_clean_fix_leaves_a_pdf_named_by_a_library_with_another_papers_dir(
    tmp_path: Path,
) -> None:
    """`export --target a | import - --target b` makes b name files in a's papers_dir."""
    from pzi.cli import run_cli

    pa, pb = tmp_path / "pa", tmp_path / "pb"
    pa.mkdir()
    pb.mkdir()
    for name in ("kept", "orphan"):
        (pa / f"{name}.pdf").write_bytes(b"%PDF-1.4\n" + name.encode())
    entry = "@article{{{k},\n  title = {{T}},\n  author = {{Smith, J}},\n  year = {{2020}},\n  file = {{{f}}},\n}}\n"
    (tmp_path / "a.bib").write_text("")
    (tmp_path / "b.bib").write_text(entry.format(k="bk", f=pa / "kept.pdf"))
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[[bibs]]\nname = "a"\npath = "{tmp_path / "a.bib"}"\npapers_dir = "{pa}"\ndefault = true\n'
        f'[[bibs]]\nname = "b"\npath = "{tmp_path / "b.bib"}"\npapers_dir = "{pb}"\n'
    )

    out, err = StringIO(), StringIO()
    run_cli(
        ["library", "clean", "--fix", "--target", "a", "--config", str(cfg)],
        home_dir=str(tmp_path), stdout=out, stderr=err,
    )

    assert (pa / "kept.pdf").read_bytes() == b"%PDF-1.4\nkept", "b's PDF was quarantined"
    # The target's own orphan is still found and filed away.
    assert not (pa / "orphan.pdf").exists()
    assert (pa / ".orphans" / "orphan.pdf").exists()


def test_clean_fix_keeps_orphans_and_creates_nothing_when_another_library_is_absent(
    tmp_path: Path,
) -> None:
    from pzi.cli import run_cli

    pa = tmp_path / "pa"
    pa.mkdir()
    (pa / "orphan.pdf").write_bytes(b"%PDF-1.4\norphan")
    (tmp_path / "a.bib").write_text("")
    ghost = tmp_path / "ghost" / "b.bib"
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[[bibs]]\nname = "a"\npath = "{tmp_path / "a.bib"}"\npapers_dir = "{pa}"\ndefault = true\n'
        f'[[bibs]]\nname = "b"\npath = "{ghost}"\npapers_dir = "{tmp_path / "pb"}"\n'
    )

    out, err = StringIO(), StringIO()
    run_cli(
        ["library", "clean", "--fix", "--target", "a", "--config", str(cfg)],
        home_dir=str(tmp_path), stdout=out, stderr=err,
    )

    assert (pa / "orphan.pdf").exists(), "an unverifiable other library must keep the PDF"
    assert not ghost.parent.exists(), "pzi created files at another library's path"
