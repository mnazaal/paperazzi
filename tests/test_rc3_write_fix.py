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


# -- citekey-scoped writers check the paper they planned against ---------------


def test_update_does_not_write_a_looked_up_record_onto_a_paper_that_took_the_key(tmp_path):
    lib = _Library(tmp_path)
    lib.bib.write_text(
        "@article{smith2024graph,\n  title = {Graph Parsers},\n  year = {2024},\n"
        "  author = {Smith, Jane},\n}\n"
    )
    b_text = (
        "@article{smith2024graph,\n  title = {Unrelated Paper B},\n  year = {2024},\n"
        "  author = {Jones, Bob},\n  url = {https://x.org/b},\n}\n"
    )

    def search(_query, *, server_url):
        lib.bib.write_text(b_text)
        return [{
            "item_type": "journalArticle",
            "record": {"title": "Graph Parsers", "venue": "Journal of Parsing",
                       "doi": "10.9/graph", "year": 2024, "authors": ["Smith, Jane"],
                       "abstract": "abstract of A"},
            "attachments": [],
        }]

    with pytest.raises(StalePlanError):
        update_service.update_bib(**_common(lib), dry_run=False, fetch_search=search)
    assert lib.bib.read_text() == b_text


def test_pdf_retry_does_not_attach_a_pdf_to_a_paper_that_took_the_key(tmp_path):
    lib = _Library(tmp_path)
    b_text = (
        "@article{target2024,\n  title = {Unrelated Paper B},\n  year = {2024},\n"
        "  author = {Jones, Bob},\n  doi = {10.1000/bbb},\n}\n"
    )

    def fetch(url):
        lib.bib.write_text(b_text)
        return _fetch(url)

    with pytest.raises(StalePlanError):
        pdf_service.retry_pdf(**_common(lib), citekey="target2024", fetch_binary=fetch)
    assert lib.bib.read_text() == b_text
    assert list(lib.papers.glob("*.pdf")) == []


def test_update_bib_entry_requires_a_stated_identity_precondition(tmp_path):
    from pzi.bib_repository import ANY_PAPER, update_bib_entry

    bib = tmp_path / "t.bib"
    bib.write_text(_entry(A, doi="10.1000/aaa"))
    with pytest.raises(TypeError):
        update_bib_entry(str(bib), "key2020", lambda e, r: e)  # type: ignore[call-arg]
    other = {"citekey": "key2020", "title": "Paper B", "doi": "10.1000/bbb"}
    with pytest.raises(StalePlanError):
        update_bib_entry(str(bib), "key2020", lambda e, r: e, expected_paper=other)
    assert update_bib_entry(
        str(bib), "key2020", lambda e, r: e, expected_paper=ANY_PAPER
    )["found"]


# -- both writers set a user-owned field: merge it, or refuse, never pick one --


def _concurrent(tmp_path, base_extra, current_extra, incoming):
    def e(**f):
        body = "".join(f"  {k} = {{{v}}},\n" for k, v in f.items())
        return (
            "@article{key2020,\n  author = {Smith, Jane},\n  title = {Paper A},\n"
            f"  year = {{2020}},\n  doi = {{10.1000/aaa}},\n{body}}}\n"
        )

    bib = tmp_path / "t.bib"
    bib.write_text(e(**base_extra))
    snap = read_bib_file(str(bib))
    record = {"citekey": "key2020", "title": "Paper A", "authors": ["Smith, Jane"],
              "year": 2020, "doi": "10.1000/aaa", **incoming}
    plan = plan_bib_write(record, snap["records"], existing_entries=snap["entries"])
    bib.write_text(e(**current_extra))
    try:
        execute_write_plan(str(bib), plan)
    except StalePlanError:
        return "refused", read_bib_file(str(bib))["records"][0]
    return "wrote", read_bib_file(str(bib))["records"][0]


def test_tags_added_by_both_writers_are_both_kept(tmp_path):
    outcome, rec = _concurrent(tmp_path, {}, {"keywords": "other"}, {"tags": ["mine"]})
    assert outcome == "wrote" and sorted(rec["tags"]) == ["mine", "other"]


def test_tags_merge_against_the_base_set(tmp_path):
    outcome, rec = _concurrent(
        tmp_path, {"keywords": "t0, t1"}, {"keywords": "t0, t1, other"}, {"tags": ["mine"]}
    )
    assert outcome == "wrote" and sorted(rec["tags"]) == ["mine", "other", "t0", "t1"]


@pytest.mark.parametrize(
    "current,incoming",
    [({"note": "other note"}, {"note": "plan note"}),
     ({"file": "/x/other.pdf"}, {"local_pdf_path": "/x/mine.pdf"})],
    ids=["note", "file"],
)
def test_a_user_owned_field_both_writers_set_differently_is_refused(tmp_path, current, incoming):
    outcome, rec = _concurrent(tmp_path, {}, current, incoming)
    assert outcome == "refused"
    for key, value in (("note", "other note"), ("local_pdf_path", "/x/other.pdf")):
        if key in rec and rec[key]:
            assert rec[key] == value


def test_a_user_owned_field_only_the_plan_changed_still_applies(tmp_path):
    outcome, rec = _concurrent(tmp_path, {}, {"abstract": "x"}, {"tags": ["mine"]})
    assert outcome == "wrote" and rec["tags"] == ["mine"]


# -- a name resolved free is not proof the bytes there are ours ----------------


@pytest.mark.parametrize("hardlinks", [True, False], ids=["hardlink", "no-hardlink"])
def test_a_writer_never_accepts_a_name_another_writer_filled_with_other_bytes(
    tmp_path, monkeypatch, hardlinks
):
    import errno
    import os

    from pzi import pdf_download

    if not hardlinks:
        def no_link(*_a, **_k):
            raise OSError(errno.EPERM, "no hardlinks")
        monkeypatch.setattr(os, "link", no_link)
    papers = tmp_path / "P"
    papers.mkdir()
    real_resolve = pdf_download.resolve_pdf_destination
    fired: list[int] = []

    def resolve(dest, data):
        out = real_resolve(dest, data)
        if not fired and data == b"%PDF-B":
            fired.append(1)
            pdf_download.write_pdf_bytes(data=b"%PDF-A", papers_dir=str(papers), citekey="k")
        return out

    monkeypatch.setattr(pdf_download, "resolve_pdf_destination", resolve)
    stored = pdf_download.write_pdf_bytes(data=b"%PDF-B", papers_dir=str(papers), citekey="k")
    assert Path(stored).read_bytes() == b"%PDF-B"
    assert sorted(p.read_bytes() for p in papers.iterdir()) == [b"%PDF-A", b"%PDF-B"]
