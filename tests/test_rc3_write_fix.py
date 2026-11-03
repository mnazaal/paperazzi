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


# -- promote replace mode must not overwrite a paper that took the key --------

from pzi import add_service, pdf_service, promote_service, update_service  # noqa: E402
from pzi.bib_repository import (  # noqa: E402
    StalePlanError,
    execute_write_plan,
    plan_bib_write,
    read_bib_file,
)
from tests.test_rc3_rollback_siblings import (  # noqa: E402
    _common,
    _fetch,
    _Library,
    _promotable_library,
    _published_search,
)

PAPER_B = (
    "@article{smith2024graph,\n  title = {Unrelated Paper B},\n  year = {2024},\n"
    "  author = {Jones, Bob},\n  doi = {10.1000/bbb},\n  journal = {Nature},\n"
    "  note = {my notes on B},\n}\n"
)


def test_promote_replace_does_not_overwrite_a_paper_that_took_the_key(tmp_path):
    lib = _Library(tmp_path)
    _promotable_library(lib)

    def search_then_b_takes_the_key(query, *, server_url):
        lib.bib.write_text(PAPER_B)
        return _published_search(query, server_url=server_url)

    result = promote_service.promote_bib(
        **_common(lib), keep_preprint=False, dry_run=False,
        fetch_search=search_then_b_takes_the_key, fetch_binary=_fetch,
    )
    assert lib.bib.read_text() == PAPER_B, lib.bib.read_text()
    assert result["summary"]["updated"] == 0, result["summary"]
    assert list(lib.papers.glob("*.pdf")) == []


def test_promote_replace_still_changes_identity_of_the_same_preprint(tmp_path):
    lib = _Library(tmp_path)
    _promotable_library(lib)
    result = promote_service.promote_bib(
        **_common(lib), keep_preprint=False, dry_run=False,
        fetch_search=_published_search, fetch_binary=_fetch,
    )
    assert result["summary"]["updated"] == 1
    assert "10.9/graph" in lib.bib.read_text()


# -- the identity rule: entries without identifiers, same-work swaps ----------


def _entry(title, **fields):
    body = "".join(f"  {k} = {{{v}}},\n" for k, v in fields.items())
    return (
        f"@article{{key2020,\n  author = {{Smith, Jane}},\n  title = {{{title}}},\n"
        f"  year = {{2020}},\n{body}}}\n"
    )


def _stale_run(tmp_path, base, current, incoming):
    bib = tmp_path / "t.bib"
    bib.write_text(base)
    snap = read_bib_file(str(bib))
    record = {"citekey": "key2020", "title": "Paper A", "authors": ["Smith, Jane"],
              "year": 2020, **incoming}
    plan = plan_bib_write(record, snap["records"], existing_entries=snap["entries"])
    assert plan["action"] == "update"
    bib.write_text(current)
    try:
        execute_write_plan(str(bib), plan)
    except StalePlanError:
        return "refused", bib.read_text()
    return "wrote", bib.read_text()


A = "Paper A"


@pytest.mark.parametrize(
    "base,current,incoming",
    [
        (_entry(A, url="https://x.org/a"), _entry("Paper B", url="https://x.org/b"),
         {"canonical_url": "https://x.org/a", "doi": "10.1000/aaa", "abstract": "abs of A"}),
        (_entry(A, url="https://x.org/a"), _entry("Paper B", doi="10.1000/bbb"),
         {"canonical_url": "https://x.org/a", "doi": "10.1000/aaa", "abstract": "abs of A"}),
    ],
    ids=["target-without-ids", "target-has-its-own-doi"],
)
def test_a_target_replaced_by_another_paper_is_refused_without_identifiers(
    tmp_path, base, current, incoming
):
    outcome, text = _stale_run(tmp_path, base, current, incoming)
    assert outcome == "refused", text
    assert text == current


@pytest.mark.parametrize(
    "name,base,current,incoming",
    [
        ("first DOI gained", _entry(A, url="https://x.org/a"),
         _entry(A, url="https://x.org/a", doi="10.1000/aaa"),
         {"canonical_url": "https://x.org/a"}),
        ("DOI case", _entry(A, doi="10.1000/aaa"), _entry(A, doi="10.1000/AAA"),
         {"doi": "10.1000/aaa"}),
        ("DOI url form", _entry(A, doi="10.1000/aaa"),
         _entry(A, doi="https://doi.org/10.1000/aaa"), {"doi": "10.1000/aaa"}),
        ("arXiv version", _entry(A, eprint="2101.00001v1", archiveprefix="arXiv"),
         _entry(A, eprint="2101.00001", archiveprefix="arXiv"), {"arxiv_id": "2101.00001"}),
        ("publisher DOI added, eprint kept",
         _entry(A, eprint="2101.00001", archiveprefix="arXiv"),
         _entry(A, eprint="2101.00001", archiveprefix="arXiv", doi="10.1000/pub"),
         {"arxiv_id": "2101.00001"}),
        ("arXiv DOI to eprint", _entry(A, doi="10.48550/arXiv.2101.00001"),
         _entry(A, eprint="2101.00001", archiveprefix="arXiv"),
         {"doi": "10.48550/arXiv.2101.00001"}),
        ("eprint to arXiv DOI", _entry(A, eprint="2101.00001", archiveprefix="arXiv"),
         _entry(A, doi="10.48550/arXiv.2101.00001"), {"arxiv_id": "2101.00001"}),
    ],
)
def test_the_same_paper_is_never_refused(tmp_path, name, base, current, incoming):
    outcome, text = _stale_run(tmp_path, base, current, {**incoming, "abstract": "abs"})
    assert outcome == "wrote", (name, text)
    assert "abs" in text


def test_same_work_swap_does_not_insert_a_duplicate_key(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    bib = tmp_path / "lib.bib"
    cfg = {"name": "t", "path": str(bib), "papers_dir": str(papers)}
    bib.write_text(_entry(A, doi="10.48550/arXiv.2101.00001").replace("key2020", "key2021"))

    def fetch(_url):
        bib.write_text(_entry(A, eprint="2101.00001", archiveprefix="arXiv").replace("key2020", "key2021"))
        return b"%PDF-1.4 x", "application/pdf"

    add_service.add_record_with_bib(
        bib=cfg, dry_run=False, fetch_binary=fetch,
        record={"citekey": "key2021", "title": A, "doi": "10.48550/arXiv.2101.00001",
                "authors": ["Smith, Jane"], "year": 2021, "pdf_url": "http://e.org/a.pdf"},
    )
    assert [e["citekey"] for e in read_bib_file(str(bib))["entries"]] == ["key2021"]


def test_add_does_not_hybridise_a_replaced_target_with_no_identifiers(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    bib = tmp_path / "lib.bib"
    cfg = {"name": "t", "path": str(bib), "papers_dir": str(papers)}
    bib.write_text(
        "@misc{key2020,\n  author = {Smith, Jane},\n  title = {Paper A about graphs},\n"
        "  url = {https://x.org/a},\n  year = {2020},\n}\n"
    )
    other = (
        "@misc{key2020,\n  author = {Jones, Bob},\n  title = {Unrelated Paper B},\n"
        "  url = {https://x.org/b},\n  year = {2020},\n  note = {mine},\n}\n"
    )

    def fetch(_url):
        bib.write_text(other)
        return b"%PDF-1.4 A", "application/pdf"

    add_service.add_record_with_bib(
        bib=cfg, dry_run=False, fetch_binary=fetch,
        record={"citekey": "key2020", "title": "Paper A about graphs",
                "canonical_url": "https://x.org/a", "doi": "10.1000/aaa",
                "abstract": "abs of A", "authors": ["Smith, Jane"], "year": 2020,
                "pdf_url": "http://e.org/a.pdf"},
    )
    entries = {e["citekey"]: e for e in read_bib_file(str(bib))["entries"]}
    b = entries["key2020"]["fields"]
    assert b["title"] == "Unrelated Paper B" and "doi" not in b and "abstract" not in b
    assert b["note"] == "mine"
