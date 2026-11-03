"""Regressions for the rc3 write-path review: reindex, promote, identity, merge, PDF names."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pzi.cli import run_cli


def _cli(home: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_cli(list(argv), home_dir=str(home), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _home_library(monkeypatch, tmp_path: Path, *, style: str, fmt: str, field_name: str):
    home = tmp_path / "home"
    (home / "Papers").mkdir(parents=True)
    for var, sub in (("XDG_CONFIG_HOME", ".c"), ("XDG_DATA_HOME", ".d"), ("XDG_CACHE_HOME", ".x")):
        monkeypatch.setenv(var, str(home / sub))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    pdf = home / "Papers" / field_name
    pdf.write_bytes(b"%PDF-1.4 A")
    shown = f"~/Papers/{field_name}" if style == "home" else str(pdf)
    bib = home / "lib.bib"
    bib.write_text(
        "@article{a2020x,\n  title = {Alpha},\n  author = {A, B},\n  year = {2020},\n"
        f"  file = {{{shown}}},\n}}\n"
    )
    cfg = home / "config.toml"
    cfg.write_text(
        f'pdf_filename_format = "{fmt}"\n[[bibs]]\nname = "t"\npath = "{bib}"\n'
        f'default = true\npapers_dir = "{home / "Papers"}"\n'
    )
    return home, bib, cfg


@pytest.mark.parametrize("style", ["home", "absolute"])
def test_filename_reindex_repoints_file_in_both_path_styles(monkeypatch, tmp_path, style):
    home, bib, cfg = _home_library(
        monkeypatch, tmp_path, style=style, fmt="{{ citekey }}-new",
        field_name="a textbraceleft x.pdf",
    )
    code, out, err = _cli(home, "library", "reindex", "--rename-files", "--config", str(cfg))
    assert (home / "Papers" / "a2020x-new.pdf").exists(), (code, out, err)
    assert not (home / "Papers" / "a textbraceleft x.pdf").exists()
    text = bib.read_text()
    assert "a2020x-new.pdf" in text and "textbraceleft" not in text, text


def test_citekey_reindex_repoints_a_home_relative_file(monkeypatch, tmp_path):
    home, bib, cfg = _home_library(
        monkeypatch, tmp_path, style="home", fmt="{{ citekey }}", field_name="old.pdf",
    )
    bib.write_text(bib.read_text().replace("a2020x", "oldkey"))
    code, out, err = _cli(home, "library", "reindex", "--rename-citekeys", "--force", "--config", str(cfg))
    pdfs = sorted(p.name for p in (home / "Papers").iterdir())
    assert "old.pdf" not in pdfs and len(pdfs) == 1, (pdfs, out, err)
    assert pdfs[0] in bib.read_text()
