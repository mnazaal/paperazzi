"""Tests for pzi.import_service — bulk BibTeX import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from pzi.bib_repository import ANY_PAPER, update_bib_entry
from pzi.import_service import import_from_bibtex

# A minimal valid config.toml so `import_from_bibtex` can resolve the bib.
MINIMAL_CONFIG_TOML = """
# pzi configuration
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib_path}"
papers_dir = "{papers_dir}"
default = true
"""


def _setup_config(td: str) -> tuple[str, str, str]:
    """Create config, bib, papers dir. Return (config_path, bib_path, papers_dir)."""
    bib_path = os.path.join(td, "library.bib")
    papers_dir = os.path.join(td, "papers")
    os.makedirs(papers_dir, exist_ok=True)
    config_path = os.path.join(td, "config.toml")
    Path(config_path).write_text(
        MINIMAL_CONFIG_TOML.format(bib_path=bib_path, papers_dir=papers_dir)
    )
    return config_path, bib_path, papers_dir


SIMPLE_BIB = (
    '@article{smith2024,\n'
    '  title = {Deep Learning},\n'
    '  author = {Smith, John},\n'
    '  year = {2024},\n'
    '  doi = {10.1000/test},\n'
    '}\n'
)

MULTI_BIB = (
    '@article{smith2024, title = {A}, author = {Smith}, year = {2024}, doi = {10.1000/1}}\n'
    '@article{jones2023, title = {B}, author = {Jones}, year = {2023}, doi = {10.1000/2}}\n'
)


def test_import_keeps_the_source_entry_type_and_booktitle() -> None:
    # Importing must not retype a conference paper as @article: the source file
    # states what the entry is, and the venue belongs in booktitle for it.
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        Path(bp).write_text("")
        src = os.path.join(td, "conference.bib")
        Path(src).write_text(
            '@inproceedings{jones2023attn, title = {Attention Revisited},\n'
            '  author = {Jones, Ada}, year = {2023}, booktitle = {NeurIPS},\n'
            '  doi = {10.1000/xyz}}\n'
        )

        result = import_from_bibtex(config_path=cp, home_dir=td, source_path=src)

        assert result["imported"] == 1
        written = Path(bp).read_text()
        assert "@inproceedings{jones2023attn" in written
        assert "booktitle = {NeurIPS}" in written
        assert "journal" not in written


def test_import_source_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        result = import_from_bibtex(
            config_path=cp, home_dir=td,
            source_path=os.path.join(td, "nonexistent.bib"),
        )
        assert result["status"] == "error"
        assert "not found" in result["message"]
        assert result["total_source"] == 0


def test_import_empty_source() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        src = os.path.join(td, "empty.bib")
        Path(src).write_text("")
        result = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src,
        )
        assert result["status"] == "ok"
        assert result["total_source"] == 0
        assert "no entries" in result["message"].lower()


def test_import_dry_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        src = os.path.join(td, "source.bib")
        Path(src).write_text(SIMPLE_BIB)
        result = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src,
            dry_run=True,
        )
        assert result["status"] == "ok"
        assert result["total_source"] == 1
        # What the real run would import. Reporting 0 here is what made
        # `import --dry-run` print `imported 0/3` for a run that does 2 — a
        # preview whose only job is that number.
        assert result["imported"] == 1
        # One result with dry_run status
        assert len(result["results"]) == 1


def test_import_single_entry() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        # Create empty target bib
        Path(bp).write_text("")
        src = os.path.join(td, "source.bib")
        Path(src).write_text(SIMPLE_BIB)
        result = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src,
        )
        assert result["status"] == "ok"
        assert result["total_source"] == 1
        assert len(result["results"]) == 1


def test_import_counts_existing_entry_as_duplicate() -> None:
    # Re-importing an entry the target already has (same DOI) is a dedup hit:
    # add returns action="update", which must be counted as a duplicate, not
    # an import. (Regression: the old code substring-matched the message.)
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        Path(bp).write_text(SIMPLE_BIB)
        src = os.path.join(td, "source.bib")
        Path(src).write_text(SIMPLE_BIB)

        result = import_from_bibtex(config_path=cp, home_dir=td, source_path=src)

        assert result["status"] == "ok"
        assert result["imported"] == 0
        assert result["skipped_duplicates"] == 1
        assert result["results"][0]["status"] == "duplicate"


def test_import_force_new_inserts_duplicate_with_suffixed_citekey() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        Path(bp).write_text(SIMPLE_BIB)
        src = os.path.join(td, "source.bib")
        Path(src).write_text(SIMPLE_BIB)

        result = import_from_bibtex(
            config_path=cp,
            home_dir=td,
            source_path=src,
            force_new=True,
        )

        assert result["status"] == "ok"
        assert result["imported"] == 1
        assert result["skipped_duplicates"] == 0
        assert result["results"][0]["citekey"] == "smith2024-2"
        target_text = Path(bp).read_text()
        assert "@article{smith2024," in target_text
        assert "@article{smith2024-2," in target_text
        # And it says so. This test asserted the doubling and never looked at
        # `warnings`, so `import --force-new` doubling a library with
        # `warnings: []` passed it — for the whole release in which
        # `import_service` claimed the silent-duplication fix had landed.
        assert any("smith2024" in w for w in result["warnings"]), result["warnings"]


def test_import_multiple_entries() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        Path(bp).write_text("")
        src = os.path.join(td, "source.bib")
        Path(src).write_text(MULTI_BIB)
        result = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src,
        )
        assert result["status"] == "ok"
        assert result["total_source"] == 2
        assert len(result["results"]) == 2


def test_batch_import_equivalent_to_repeated_single_writes(tmp_path) -> None:
    """The bulk write path must produce byte-identical results to looping the
    single-write path: same final .bib and the same per-record actions, including
    dedup against the library *and* against records added earlier in the run."""
    from pzi.add_service import add_record_with_bib, add_records_to_bib_batch

    seed = (
        "@article{seed2020,\n"
        "  author = {Anderson, A},\n"
        "  doi = {10.1000/seed},\n"
        "  title = {Seed Paper},\n"
        "  year = {2020},\n"
        "}\n"
    )
    records: list[dict[str, object]] = [
        {"citekey": "alpha", "title": "Alpha Paper",
         "authors": ["Brown, B"], "year": 2021, "doi": "10.1000/alpha"},
        {"citekey": "seeddup", "title": "Seed Paper Revised",
         "authors": ["Anderson, A"], "year": 2020, "doi": "10.1000/seed"},  # dedup vs library
        {"citekey": "beta", "title": "Beta Paper",
         "authors": ["Clark, C"], "year": 2022, "doi": "10.1000/beta"},
        {"citekey": "alphadup", "title": "Alpha Paper",
         "authors": ["Brown, B"], "year": 2021, "doi": "10.1000/alpha"},  # dedup vs record 0
    ]

    def _make_bib(name: str):
        d = tmp_path / name
        (d / "papers").mkdir(parents=True)
        bib_path = d / "library.bib"
        bib_path.write_text(seed)
        bib = {"name": "main", "path": str(bib_path),
               "papers_dir": str(d / "papers"), "default": True}
        return bib, bib_path

    single_bib, single_path = _make_bib("single")
    batch_bib, batch_path = _make_bib("batch")

    single_actions = [
        add_record_with_bib(bib=single_bib, record=dict(rec), dry_run=False)["action"]
        for rec in records
    ]
    batch_actions = [
        r["action"]
        for r in add_records_to_bib_batch(
            bib=batch_bib, records=[dict(r) for r in records], dry_run=False,
        )
    ]

    assert batch_actions == single_actions
    assert batch_actions == ["insert", "update", "insert", "update"]
    assert single_path.read_text() == batch_path.read_text()


def test_import_reports_source_blocks_the_parser_dropped(tmp_path) -> None:
    """Unreadable source entries must not vanish from a successful-looking import.

    The old `except` around the parse was dead code — v2 collects malformed
    blocks rather than raising — so an import of a partly-broken file reported
    `imported N/N` and exited 0, having silently skipped the rest.
    """
    d = tmp_path / "lib"
    (d / "papers").mkdir(parents=True)
    bib_path = d / "library.bib"
    bib_path.write_text("")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "main"\npath = "{bib_path}"\n'
        f'papers_dir = "{d / "papers"}"\ndefault = true\n'
    )
    source = tmp_path / "source.bib"
    source.write_text(
        "@article{good2024, title = {Good}, author = {A}, year = {2024}}\n\n"
        "@article{broken2024,\n  title = {Unclosed\n  year = {2024},\n}\n"
    )

    result = import_from_bibtex(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        source_path=str(source),
        bib_selector=None,
        dry_run=False,
    )

    assert result["imported"] == 1
    # The unreadable block is counted, not quietly excluded from the total.
    assert result["total_source"] == 2
    assert result["skipped_errors"] == 1
    assert any("unparseable" in err.lower() for err in result["errors"])


def test_import_skips_a_source_entry_whose_field_name_swallowed_a_comment(
    tmp_path,
) -> None:
    """Importing it wrote the mangled key into the library and bricked it.

    The block parses, so the round-trip gate sees nothing wrong; the hidden
    field's value ends up under a name no reader matches, and every subsequent
    write to the library is refused. It is reported once, skipped, and the good
    entry beside it still imports.
    """
    d = tmp_path / "lib"
    (d / "papers").mkdir(parents=True)
    bib_path = d / "library.bib"
    bib_path.write_text("")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "main"\npath = "{bib_path}"\n'
        f'papers_dir = "{d / "papers"}"\ndefault = true\n'
    )
    source = tmp_path / "source.bib"
    source.write_text(
        "@article{good2024, title = {Good}, author = {A}, year = {2024}}\n\n"
        "@article{foreign2020a,\n"
        "  title = {Some title},\n"
        "  % private note\n"
        "  doi = {10.1000/xyz},\n"
        "  year = {2020},\n"
        "}\n"
    )

    result = import_from_bibtex(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        source_path=str(source),
        bib_selector=None,
        dry_run=False,
    )

    assert result["imported"] == 1
    assert result["total_source"] == 2
    assert result["skipped_errors"] == 1
    # Reported once, naming the entry and the field it hides.
    matching = [err for err in result["errors"] if "foreign2020a" in err]
    assert len(matching) == 1
    assert "doi" in matching[0]

    written = bib_path.read_text(encoding="utf-8")
    assert "foreign2020a" not in written
    assert "% private note" not in written
    # The library still accepts writes.
    update_bib_entry(
        str(bib_path),
        "good2024",
        lambda entry, _record: {**entry, "fields": {**entry["fields"], "keywords": "x"}},
    
        expected_paper=ANY_PAPER,
    )


def test_import_of_a_wholly_unparseable_source_is_an_error(tmp_path) -> None:
    d = tmp_path / "lib"
    (d / "papers").mkdir(parents=True)
    bib_path = d / "library.bib"
    bib_path.write_text("")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "main"\npath = "{bib_path}"\n'
        f'papers_dir = "{d / "papers"}"\ndefault = true\n'
    )
    source = tmp_path / "source.bib"
    source.write_text("@article{broken2024,\n  title = {Unclosed\n")

    result = import_from_bibtex(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        source_path=str(source),
        bib_selector=None,
        dry_run=False,
    )

    assert result["status"] == "error"
    assert result["errors"]


def test_batch_import_dry_run_predicts_the_actions_the_real_run_takes(tmp_path) -> None:
    """A preview that does not see its own earlier records predicts the wrong plan.

    Dry-run skipped `session.apply_plan`, so record K was matched against the
    library alone instead of the library plus records 1..K-1 — contradicting the
    batch path's documented contract. Two records sharing a DOI were both
    previewed as inserts where the real run inserts then updates.
    """
    from pzi.add_service import add_records_to_bib_batch

    d = tmp_path / "lib"
    (d / "papers").mkdir(parents=True)
    bib_path = d / "library.bib"
    bib_path.write_text("")
    bib = {"name": "main", "path": str(bib_path),
           "papers_dir": str(d / "papers"), "default": True}

    records: list[dict[str, object]] = [
        {"citekey": "alpha", "title": "Alpha Paper",
         "authors": ["Brown, B"], "year": 2021, "doi": "10.1000/alpha"},
        {"citekey": "alphadup", "title": "Alpha Paper Revised",
         "authors": ["Brown, B"], "year": 2021, "doi": "10.1000/alpha"},
        {"citekey": "beta", "title": "Beta Paper",
         "authors": ["Clark, C"], "year": 2022, "doi": "10.1000/beta"},
    ]

    predicted = [
        r["action"]
        for r in add_records_to_bib_batch(
            bib=bib, records=[dict(r) for r in records], dry_run=True,
        )
    ]
    # The preview wrote nothing, so the real run starts from the same state.
    assert bib_path.read_text() == ""

    actual = [
        r["action"]
        for r in add_records_to_bib_batch(
            bib=bib, records=[dict(r) for r in records], dry_run=False,
        )
    ]

    assert predicted == actual
    assert actual == ["insert", "update", "insert"]


def test_batch_import_parity_for_citekey_collision_and_pdf_reuse(tmp_path) -> None:
    """Bulk and repeated-single paths must agree on the trickier cases too:
    a citekey collision between two *distinct* papers (suffix, not dedup) and
    PDF reuse when a later record is an exact duplicate of an earlier one."""
    from pzi.add_service import add_record_with_bib, add_records_to_bib_batch

    def _make_fetch_binary():
        downloads = {"n": 0}

        def _fetch(url: str):
            downloads["n"] += 1
            return (b"%PDF-1.7\nbody", "application/pdf")

        return _fetch, downloads

    records: list[dict[str, object]] = [
        {"citekey": "dup", "title": "First Paper", "authors": ["Brown, B"],
         "year": 2021, "doi": "10.1000/a", "pdf_url": "https://example.com/a.pdf"},
        {"citekey": "dup", "title": "Second Paper", "authors": ["Clark, C"],
         "year": 2022, "doi": "10.1000/b"},  # same citekey, different paper -> suffix
        {"citekey": "ignored", "title": "First Paper", "authors": ["Brown, B"],
         "year": 2021, "doi": "10.1000/a"},  # exact dupe of record 0 -> reuse key + PDF
    ]

    def _make_bib(name: str):
        d = tmp_path / name
        (d / "papers").mkdir(parents=True)
        bib_path = d / "library.bib"
        bib_path.write_text("")
        bib = {"name": "main", "path": str(bib_path),
               "papers_dir": str(d / "papers"), "default": True}
        return bib, bib_path, d / "papers"

    single_bib, single_path, single_papers = _make_bib("single")
    batch_bib, batch_path, batch_papers = _make_bib("batch")

    # Relative file paths so the only legitimate per-bib difference (the
    # absolute papers_dir prefix) doesn't mask a real divergence.
    single_fetch, single_dl = _make_fetch_binary()
    single_results = [
        add_record_with_bib(
            bib=single_bib, record=dict(rec), dry_run=False,
            fetch_binary=single_fetch, file_path_style="relative",
        )
        for rec in records
    ]
    batch_fetch, batch_dl = _make_fetch_binary()
    batch_results = add_records_to_bib_batch(
        bib=batch_bib, records=[dict(r) for r in records], dry_run=False,
        fetch_binary=batch_fetch, file_path_style="relative",
    )

    assert [r["action"] for r in batch_results] == [r["action"] for r in single_results]
    assert [r["citekey"] for r in batch_results] == [r["citekey"] for r in single_results]
    assert [r["citekey"] for r in single_results] == ["dup", "dup-2", "dup"]
    assert single_path.read_text() == batch_path.read_text()
    # Same number of downloads and stored PDFs across both paths (record 2
    # reuses record 0's PDF rather than re-downloading).
    assert single_dl["n"] == batch_dl["n"] == 1
    assert len(list(single_papers.glob("*.pdf"))) == len(list(batch_papers.glob("*.pdf"))) == 1


def test_import_invalid_bibtex() -> None:
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        src = os.path.join(td, "bad.bib")
        Path(src).write_text("not valid bibtex {{{{")
        result = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src,
        )
        # bibtexparser v2 is lenient — invalid text yields 0 entries
        assert result["total_source"] == 0


def test_reimporting_a_file_reports_that_it_doubled_the_library() -> None:
    """The writer reports a near-duplicate as a *warning*, and this module
    never read them — the string `warnings` did not occur in it.

    So importing the same file twice inserted `good1-2` beside `good1` and said
    nothing. Silently doubling the library is the one thing `import` must not
    do quietly.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        src = os.path.join(td, "source.bib")
        Path(src).write_text(SIMPLE_BIB)

        first = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src, dry_run=False
        )
        assert first["status"] == "ok"

        second = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src, dry_run=False
        )

    # Either it was recognised as a duplicate, or it was inserted again and
    # said so. What it must not do is insert and stay silent.
    inserted_again = second["imported"] > 0
    assert (not inserted_again) or second["warnings"], second


def test_a_dry_run_classifies_exactly_as_the_run_it_previews() -> None:
    """The preview counted every ok result as an import.

    So a source whose entries the library already has previewed
    `imported 2/2` and then ran as `imported 1/2, skipped 1 duplicates` — the
    one number a preview exists to get right, wrong in the direction that makes
    the user proceed.
    """
    with tempfile.TemporaryDirectory() as td:
        cp, bp, _pd = _setup_config(td)
        Path(bp).write_text(SIMPLE_BIB)
        src = os.path.join(td, "source.bib")
        # One entry the library already has, enriched so it counts as an update,
        # and one it does not.
        Path(src).write_text(
            SIMPLE_BIB.replace("}\n", "  abstract = {Newly supplied},\n}\n", 1)
            + "\n@article{fresh2022,\n  title = {Something New},\n"
            "  author = {Other, B},\n  year = {2022},\n}\n"
        )

        preview = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src, dry_run=True
        )
        applied = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src, dry_run=False
        )

        for key in ("imported", "updated", "skipped_duplicates", "skipped_errors"):
            assert preview.get(key) == applied.get(key), (
                f"{key}: preview {preview.get(key)} != applied {applied.get(key)}"
            )
        # Same classification per entry, differing only in tense.
        assert [r["status"] for r in preview["results"]] == ["would_update", "would_import"]
        assert [r["status"] for r in applied["results"]] == ["updated", "imported"]


def test_import_from_bibtex_batch_write_failure_is_unavailable(monkeypatch) -> None:
    """G3: a batch write that raises after validation passed is an external
    dependency failing, not a bad request — distinct from the resolution
    failure above (REASON_CONFIG) and from a per-record validation error."""
    import pzi.import_service as import_service_module

    with tempfile.TemporaryDirectory() as td:
        cp, bp, pd = _setup_config(td)
        Path(bp).write_text("")
        src = os.path.join(td, "source.bib")
        Path(src).write_text(SIMPLE_BIB)

        def _boom(**_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(import_service_module, "add_records_to_bib_batch", _boom)

        result = import_from_bibtex(
            config_path=cp, home_dir=td, bib_selector=None,
            source_path=src, dry_run=False, force_new=False,
        )

    assert result["status"] == "error"
    assert result["message"] == "failed to write imported records"
    from pzi.errors import REASON_UNAVAILABLE

    assert result["reason"] == REASON_UNAVAILABLE


# ── a source entry's attachments survive the copy into the library ────────────


def _import_dual_attachment(
    td: str, file_value: str, *, existing: str = "", home_dir: str | None = None
) -> tuple[dict, str]:
    """Import one entry whose `file` is *file_value*; return (result, written file field)."""
    from pzi.bib_repository import read_bib_file

    cp, bp, _pd = _setup_config(td)
    Path(bp).write_text(existing)
    src = os.path.join(td, "src", "export.bib")
    Path(src).write_text(
        "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
        "  year = {2020}, doi = {10.1000/dual},\n"
        "  file = {" + file_value + "}}\n"
    )
    result = import_from_bibtex(
        config_path=cp, home_dir=home_dir or td, source_path=src
    )
    entries = read_bib_file(bp)["entries"]
    assert len(entries) == 1, entries
    return dict(result), entries[0]["fields"].get("file", "")


def _two_source_pdfs(td: str) -> tuple[str, str]:
    source_dir = os.path.join(td, "src")
    os.makedirs(source_dir)
    first = os.path.join(source_dir, "a.pdf")
    second = os.path.join(source_dir, "b.pdf")
    Path(first).write_bytes(b"%PDF-1.4\n")
    Path(second).write_bytes(b"%PDF-1.4\nsecond")
    return first, second


def test_import_keeps_every_attachment_of_an_inserted_entry() -> None:
    """`file = {a.pdf;b.pdf}` was imported as `file = {a.pdf}`, reported ok.

    `file` is record-owned and the record carries only the primary path, so the
    projection merged onto the source entry replaced the whole field with it.
    """
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)

        result, written = _import_dual_attachment(td, f"{first};{second}")

        assert result["imported"] == 1, result
        assert written == f"{first};{second}", written


def test_import_keeps_a_zotero_composite_verbatim() -> None:
    """The description, mimetype and second attachment are the source's, not pzi's."""
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        composite = (
            f"Full Text PDF:{first}:application/pdf;"
            f"Supplement:{second}:application/pdf"
        )

        result, written = _import_dual_attachment(td, composite)

        assert result["imported"] == 1, result
        assert written == composite, written


def test_import_merged_into_an_entry_without_a_file_keeps_every_attachment() -> None:
    """The update path: the incoming record supplies the attachment the entry lacked."""
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        existing = (
            "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
            "  year = {2020}, doi = {10.1000/dual}}\n"
        )

        result, written = _import_dual_attachment(
            td, f"{first};{second}", existing=existing
        )

        assert result.get("updated") == 1, result
        assert written == f"{first};{second}", written


def test_import_merged_into_an_entry_with_its_own_file_keeps_that_file() -> None:
    """An entry that already names its attachment is not re-pointed by an import."""
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        own = os.path.join(td, "papers", "own.pdf")
        os.makedirs(os.path.dirname(own))
        Path(own).write_bytes(b"%PDF-1.4\nown")
        existing = (
            "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
            "  year = {2020}, doi = {10.1000/dual},\n"
            "  file = {" + own + "}}\n"
        )

        _result, written = _import_dual_attachment(
            td, f"{first};{second}", existing=existing
        )

        assert written == own, written


def test_import_onto_an_entry_with_a_composite_keeps_its_own_attachments() -> None:
    """Matching primaries must not hand the entry's `file` field to the source.

    The entry's Notes attachment, descriptions and mimetypes were replaced by
    the source's `a.pdf;b.pdf`, and the run reported a skipped duplicate.
    """
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        notes = os.path.join(td, "src", "n.txt")
        Path(notes).write_text("notes")
        own = f"Full Text PDF:{first}:application/pdf;Notes:{notes}:text/plain"
        existing = (
            "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
            "  year = {2020}, doi = {10.1000/dual},\n"
            "  file = {" + own + "}}\n"
        )

        _result, written = _import_dual_attachment(
            td, f"{first};{second}", existing=existing
        )

        assert written == own, written


@pytest.mark.parametrize("dry_run", [True, False])
def test_import_onto_an_entry_naming_the_same_primary_changes_nothing(
    dry_run: bool,
) -> None:
    """A duplicate must not rewrite the entry, the preview must agree, and the
    source attachment the entry did not take is reported, not dropped silently.

    The PDFs sit under `$HOME` (pinned by conftest), which is the only place a
    `~/` fold could show: under a separate temp dir this test could not see it.
    """
    home = Path.home()
    with tempfile.TemporaryDirectory(dir=home) as td:
        first, second = _two_source_pdfs(td)
        existing = (
            "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
            "  year = {2020}, doi = {10.1000/dual},\n"
            "  file = {" + first + "}}\n"
        )
        cp, bp, _pd = _setup_config(td)
        Path(bp).write_text(existing)
        before = Path(bp).read_bytes()
        src = os.path.join(td, "src", "export.bib")
        Path(src).write_text(
            existing.replace("{" + first + "}", "{" + f"{first};{second}" + "}")
        )

        result = import_from_bibtex(
            config_path=cp, home_dir=str(home), source_path=src, dry_run=dry_run
        )

        assert result["skipped_duplicates"] == 1, result
        assert any(
            "not merged from the source" in w and second in w
            for w in result.get("warnings", [])
        ), result.get("warnings")
        from pzi.bib_repository import read_bib_file

        written = read_bib_file(bp)["entries"][0]["fields"]["file"]
        assert written == first, written
        assert Path(bp).read_bytes() == before


def test_import_keeps_a_multi_attachment_field_in_the_source_spelling() -> None:
    """`pdf_file_path_style` folds a single path only; a composite stays verbatim.

    Folding each component would mean rewriting another producer's escaping
    (`plan_file_field_fold` refuses composites as lossy). So under `$HOME` a
    two-attachment field keeps its absolute spelling while a one-attachment
    field is folded to `~/` — a deliberate, pinned difference.
    """
    home = Path.home()
    with tempfile.TemporaryDirectory(dir=home) as td:
        first, second = _two_source_pdfs(td)
        _result, multi = _import_dual_attachment(
            td, f"{first};{second}", home_dir=str(home)
        )
        assert multi == f"{first};{second}", multi

    with tempfile.TemporaryDirectory(dir=home) as td:
        first, _second = _two_source_pdfs(td)
        _result, single = _import_dual_attachment(td, first, home_dir=str(home))
        assert single == "~/" + os.path.relpath(first, home), single


def test_import_warns_about_source_attachments_a_merge_left_out() -> None:
    """Real run, outside HOME (no `~/` fold involved): the warning is present."""
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        existing = (
            "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
            "  year = {2020}, doi = {10.1000/dual},\n"
            "  file = {" + first + "}}\n"
        )

        result, written = _import_dual_attachment(
            td, f"{first};{second}", existing=existing
        )

        assert written == first, written
        assert [w for w in result["warnings"] if "not merged" in w] == [
            f"dual: kept the library entry's own file field; not merged from the "
            f"source: {second}"
        ], result["warnings"]


def test_import_is_silent_when_the_merge_left_nothing_out() -> None:
    """The same file under another spelling is not 'left out'."""
    with tempfile.TemporaryDirectory() as td:
        first, _second = _two_source_pdfs(td)
        existing = (
            "@article{dual, title = {Two Attachments}, author = {Doe, Jane},\n"
            "  year = {2020}, doi = {10.1000/dual},\n"
            "  file = {Full Text PDF:" + first + ":application/pdf}}\n"
        )

        result, _written = _import_dual_attachment(td, first, existing=existing)

        assert [w for w in result["warnings"] if "not merged" in w] == [], result[
            "warnings"
        ]


def test_an_unresolvable_home_in_a_library_file_field_does_not_fail_the_import() -> None:
    """`~unknownuser/` makes `expanduser` raise after the commit; that is a warning."""
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        cp, bp, _pd = _setup_config(td)
        Path(bp).write_text(
            "@article{dual, title = {Two}, author = {Doe, Jane}, year = {2020},\n"
            "  doi = {10.1000/dual}, file = {~nosuchuser_pzi_zz/x.pdf}}\n"
        )
        src = os.path.join(td, "src", "export.bib")
        Path(src).write_text(
            "@article{dual, title = {Two}, author = {Doe, Jane}, year = {2020},\n"
            "  doi = {10.1000/dual}, file = {" + f"{first};{second}" + "}}\n"
            "@article{new1, title = {New}, author = {Roe, R}, year = {2021},\n"
            "  doi = {10.1000/new1}}\n"
        )

        result = import_from_bibtex(config_path=cp, home_dir=td, source_path=src)

        assert result["status"] == "ok", result
        assert result["imported"] == 1, result
        assert any(w.startswith("dual: could not compare") for w in result["warnings"])


def test_one_unresolvable_entry_keeps_the_other_entries_warnings() -> None:
    """`~unknownuser/` on one merged entry must not discard another's warning."""
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        cp, bp, _pd = _setup_config(td)
        kept = os.path.join(td, "kept.pdf")
        Path(kept).write_bytes(b"%PDF-1.4\nkept")
        Path(bp).write_text(
            "@article{good, title = {Good}, author = {Doe, Jane}, year = {2020},\n"
            "  doi = {10.1000/good}, file = {" + kept + "}}\n"
            "@article{odd, title = {Odd}, author = {Roe, R}, year = {2019},\n"
            "  doi = {10.1000/odd}, file = {~nosuchuser_pzi_zz/x.pdf}}\n"
        )
        src = os.path.join(td, "src", "export.bib")
        Path(src).write_text(
            "@article{good, title = {Good}, author = {Doe, Jane}, year = {2020},\n"
            "  doi = {10.1000/good}, file = {" + f"{first};{second}" + "}}\n"
            "@article{odd, title = {Odd}, author = {Roe, R}, year = {2019},\n"
            "  doi = {10.1000/odd}, file = {" + first + "}}\n"
        )

        result = import_from_bibtex(config_path=cp, home_dir=td, source_path=src)

        assert result["status"] == "ok", result
        assert any(w.startswith("good: kept the library entry's own file field")
                   for w in result["warnings"]), result["warnings"]
        assert any(w.startswith("odd: could not compare") for w in result["warnings"])


def _import_with_relative_source(td: str, file_value: str, style: str) -> str:
    """Import an entry whose `file` is relative to a source dir distinct from the library's.

    Returns the written `file` field. The library sits in `td/lib`, the source
    in `td/source`, so a field resolved at the destination points nowhere.
    """
    from pzi.bib_repository import read_bib_file

    lib = os.path.join(td, "lib")
    papers = os.path.join(lib, "papers")
    os.makedirs(papers)
    bp = os.path.join(lib, "library.bib")
    Path(bp).write_text("")
    cp = os.path.join(td, "config.toml")
    Path(cp).write_text(
        MINIMAL_CONFIG_TOML.format(bib_path=bp, papers_dir=papers)
        + f'pdf_file_path_style = "{style}"\n'
    )
    src_dir = os.path.join(td, "source")
    os.makedirs(os.path.join(src_dir, "papers"))
    for name in ("original.pdf", "second.pdf"):
        Path(src_dir, "papers", name).write_bytes(b"%PDF-1.4\n" + name.encode())
    src = os.path.join(src_dir, "export.bib")
    Path(src).write_text(
        "@article{rel, title = {Relative}, author = {Doe, Jane},\n"
        "  year = {2020}, doi = {10.1000/rel},\n"
        "  file = {" + file_value + "}}\n"
    )
    import_from_bibtex(config_path=cp, home_dir=td, source_path=src)
    return read_bib_file(bp)["entries"][0]["fields"].get("file", "")


def _located(td: str, written: str, bib_dir: str) -> list[Path]:
    from pzi.bibtex import parse_file_field

    out = []
    for p in parse_file_field(written):
        p = p.replace("~", td, 1) if p.startswith("~") else p
        out.append((Path(bib_dir) / p).resolve())
    return out


@pytest.mark.parametrize("style", ["home", "relative", "absolute"])
def test_import_resolves_a_relative_attachment_at_the_source(style: str) -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        written = _import_with_relative_source(td, "papers/original.pdf", style)
        target = Path(td, "source", "papers", "original.pdf").resolve()
        assert _located(td, written, os.path.join(td, "lib")) == [target], written


def test_import_resolves_every_relative_component_of_a_composite_at_the_source() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        written = _import_with_relative_source(
            td,
            "Full Text:papers/original.pdf:application/pdf;"
            "Extra:papers/second.pdf:application/pdf",
            "home",
        )
        base = Path(td, "source", "papers")
        assert written == (
            f"Full Text:{base}/original.pdf:application/pdf;"
            f"Extra:{base}/second.pdf:application/pdf"
        ), written


def test_import_resolves_a_relative_multi_attachment_field_and_keeps_its_spelling() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        written = _import_with_relative_source(
            td, "papers/original.pdf;papers/second.pdf", "home"
        )
        base = Path(td, "source", "papers")
        assert written == f"{base}/original.pdf;{base}/second.pdf", written


_DUP_ENTRY = (
    "@article{k, title = {Deep Learning}, author = {Doe, Jane}, year = {2020},"
    " doi = {10.1000/abc}%s}\n"
)


def _duplicate_import_bytes(
    td: str, lib_file: str, src_file: str | None, extra: tuple[str, str] = ("", "")
) -> tuple[bytes, bytes]:
    """Library bytes before and after importing an entry the library already has.

    *lib_file* / *src_file* are `file` field values (or None); *extra* is an
    additional field line for the library and the source respectively.
    """
    lib = os.path.join(td, "lib")
    papers = os.path.join(lib, "papers")
    os.makedirs(papers)
    Path(papers, "x.pdf").write_bytes(b"%PDF-1.4\nx")
    bp = os.path.join(lib, "library.bib")

    def entry(file_value: str | None, more: str) -> str:
        fields = (f", file = {{{file_value}}}" if file_value else "") + more
        return _DUP_ENTRY % fields

    Path(bp).write_text(entry(lib_file, extra[0]))
    cp = os.path.join(td, "config.toml")
    Path(cp).write_text(MINIMAL_CONFIG_TOML.format(bib_path=bp, papers_dir=papers))
    os.makedirs(os.path.join(td, "src"))
    src = os.path.join(td, "src", "export.bib")
    Path(src).write_text(entry(src_file, extra[1]))
    before = Path(bp).read_bytes()
    import_from_bibtex(config_path=cp, home_dir=td, source_path=src)
    return before, Path(bp).read_bytes()


def test_a_duplicate_import_leaves_a_relative_file_field_alone() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        before, after = _duplicate_import_bytes(
            td, "papers/x.pdf", f"{td}/lib/papers/x.pdf"
        )
        assert after == before


def test_a_duplicate_import_leaves_a_composite_file_field_alone() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        value = f"Full Text:{td}/lib/papers/x.pdf:application/pdf"
        before, after = _duplicate_import_bytes(td, value, value)
        assert after == before


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (("", ", abstract = {An abstract.}"), "An abstract."),
        ((", keywords = {a}", ", keywords = {b}"), "b"),
        (("", ", url = {https://e.org/x}"), "https://e.org/x"),
        (("", ", journal = {JMLR}"), "JMLR"),
    ],
)
def test_a_duplicate_import_with_a_real_change_is_still_written(
    extra: tuple[str, str], expected: str
) -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        before, after = _duplicate_import_bytes(td, None, None, extra)
        assert after != before
        assert expected in after.decode()


def test_a_duplicate_import_that_adds_a_file_is_still_written() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        before, after = _duplicate_import_bytes(td, None, f"{td}/lib/papers/x.pdf")
        assert after != before
        assert "x.pdf" in after.decode()


def _import_source_file_value(
    td: str, file_value: str, files: tuple[str, ...] = (), *, via_symlink: bool = False
) -> tuple[str, list[Path]]:
    """Import one entry carrying *file_value* from `td/real/inner/export.bib`.

    *files* are created relative to `td/real/inner`. With *via_symlink* the source
    path is given through a symlink `td/link` -> `td/real/inner`. Returns the
    written `file` field and where each of its attachments lies on disk.
    """
    from pzi.bib_repository import read_bib_file
    from pzi.bib_serialize import resolved_attachment_paths

    lib = os.path.join(td, "lib")
    papers = os.path.join(lib, "papers")
    os.makedirs(papers)
    bp = os.path.join(lib, "library.bib")
    Path(bp).write_text("")
    cp = os.path.join(td, "config.toml")
    Path(cp).write_text(
        MINIMAL_CONFIG_TOML.format(bib_path=bp, papers_dir=papers)
        + 'pdf_file_path_style = "home"\n'
    )
    inner = Path(td, "real", "inner")
    inner.mkdir(parents=True)
    for name in files:
        target = inner / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4\n")
    (inner / "export.bib").write_text(
        "@article{rel, title = {Relative}, author = {Doe, Jane},\n"
        "  year = {2020}, doi = {10.1000/rel},\n"
        "  file = {" + file_value + "}}\n"
    )
    source = inner / "export.bib"
    if via_symlink:
        Path(td, "link").symlink_to(inner)
        source = Path(td, "link", "export.bib")
    import_from_bibtex(config_path=cp, home_dir=td, source_path=str(source))
    entry = read_bib_file(bp)["entries"][0]
    located = [
        Path(p).expanduser() for p in resolved_attachment_paths(entry, bp)
    ]
    return entry["fields"].get("file", ""), located


@pytest.mark.parametrize(
    "value",
    [
        r":https\://example.com/x.pdf:PDF",
        "https://example.com/x.pdf",
        "Online:https\\://example.com/x.pdf:URL",
        "file:///home/me/x.pdf",
        r"C:\Users\me\x.pdf",
        r"Full Text:C\:\\Users\\me\\x.pdf:application/pdf",
    ],
)
def test_import_keeps_a_url_or_drive_path_attachment_byte_identical(value: str) -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        written, _ = _import_source_file_value(td, value)
        assert written == value


def test_import_rewrites_the_path_component_not_an_equal_description() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        written, located = _import_source_file_value(
            td, "x.pdf:x.pdf:application/pdf", ("x.pdf",)
        )
        assert written.startswith("x.pdf:"), written
        assert written.endswith(":application/pdf"), written
        assert located == [Path(td, "real", "inner", "x.pdf")]
        assert located[0].exists()


def test_import_resolves_a_semicolon_bearing_filename_at_the_source() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        name = "papers/Metric Elicitation; Moving.pdf"
        _, located = _import_source_file_value(td, name, (name,))
        assert [p.exists() for p in located] == [True], located


def test_import_resolves_dotdot_as_the_kernel_does_through_a_symlinked_source() -> None:
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        Path(td, "real", "papers").mkdir(parents=True)
        Path(td, "real", "papers", "x.pdf").write_bytes(b"%PDF-1.4\n")
        _, located = _import_source_file_value(
            td, "../papers/x.pdf", via_symlink=True
        )
        assert [p.exists() for p in located] == [True], located


@pytest.mark.parametrize(
    "value",
    [r"arXiv\:2101.00001.pdf", "ab:c.pdf", "a:b.pdf", r"doi\:10.1000/paper.pdf"],
)
def test_a_colon_in_a_relative_filename_is_not_read_as_a_url(value: str) -> None:
    """Only a scheme or drive letter followed by a colon AND a slash is not local.

    `[A-Za-z][A-Za-z0-9+.-]+:` matched `arXiv\\:2101.00001.pdf`, so a real file
    beside the source was left relative and resolved against the library.
    """
    from pzi.bibtex import parse_file_field

    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        name = parse_file_field(value)[0]
        _, located = _import_source_file_value(td, value, (name,))
        assert [p.exists() for p in located] == [True], located


def test_a_plain_relative_attachment_keeps_a_symlinked_source_dir_spelling(
    tmp_path: Path, monkeypatch
) -> None:
    """Symlinks are resolved only for `..`; otherwise the spelling is kept.

    Resolving every path rewrote `~/src/papers/x.pdf` under a symlinked HOME as
    the link's target, outside `~`, so the home style no longer applied.
    """
    real = tmp_path / "realhome"
    (real / "src" / "papers").mkdir(parents=True)
    (real / "src" / "papers" / "x.pdf").write_bytes(b"%PDF-1.4\nx")
    home = tmp_path / "home"
    home.symlink_to(real)
    monkeypatch.setenv("HOME", str(home))
    from pzi.import_service import _resolve_like_the_kernel

    resolved = _resolve_like_the_kernel(str(home / "src"), "papers/x.pdf")

    assert resolved == str(home / "src" / "papers" / "x.pdf")
    assert Path(resolved).exists()


@pytest.mark.parametrize(
    "value",
    [r":doi\:10.1000/xyz:URL", "mailto:a@b.org", r"PDF:mailto\:a@b.org:URL", "tel:+123"],
)
def test_a_uri_with_no_slash_is_kept_as_written(value: str) -> None:
    """`doi:`/`mailto:` URIs look like colon filenames; no such file means a URI."""
    with tempfile.TemporaryDirectory() as raw:
        td = str(Path(raw).resolve())
        written, _ = _import_source_file_value(td, value)
        assert written == value


def test_a_dotdot_attachment_keeps_a_symlinked_home_spelling(
    tmp_path: Path, monkeypatch
) -> None:
    """`..` resolved by the kernel, but spelled lexically when that is the same file."""
    real = tmp_path / "realhome"
    (real / "src").mkdir(parents=True)
    (real / "pp").mkdir()
    (real / "pp" / "x.pdf").write_bytes(b"%PDF-1.4\nx")
    home = tmp_path / "home"
    home.symlink_to(real)
    from pzi.import_service import _resolve_like_the_kernel

    resolved = _resolve_like_the_kernel(str(home / "src"), "../pp/x.pdf")

    assert resolved == str(home / "pp" / "x.pdf")


def test_stdin_import_warns_naming_entries_with_relative_attachments(
    tmp_path: Path,
) -> None:
    """Stdin has no folder: relative paths are kept as written and named.

    Owner decision 2026-11-25 ("warn and document"): no guessed base. Absolute
    paths and URLs need no base, so their entries are not named.
    """
    cp, bp, _papers = _setup_config(str(tmp_path))
    Path(bp).write_text("")
    text = (
        "@article{rel1, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1/a},\n"
        "  file = {papers/a.pdf}}\n"
        "@article{rel2, title = {B}, author = {Doe, J}, year = {2020}, doi = {10.1/b},\n"
        "  file = {Full Text:papers/b.pdf:application/pdf}}\n"
        "@article{abs, title = {C}, author = {Doe, J}, year = {2020}, doi = {10.1/c},\n"
        "  file = {/x/c.pdf}}\n"
        "@article{url, title = {D}, author = {Doe, J}, year = {2020}, doi = {10.1/d},\n"
        "  file = {https://example.org/d.pdf}}\n"
    )

    result = import_from_bibtex(
        config_path=cp, home_dir=str(tmp_path), source_path="<stdin>", source_text=text
    )

    stdin_warnings = [w for w in result.get("warnings", []) if "stdin" in w]
    assert len(stdin_warnings) == 1, result.get("warnings")
    assert "2 entr(ies)" in stdin_warnings[0]
    assert "rel1, rel2" in stdin_warnings[0]
    assert "abs" not in stdin_warnings[0] and "url" not in stdin_warnings[0]
    from pzi.bib_repository import read_bib_file

    files = {e["citekey"]: e["fields"].get("file") for e in read_bib_file(bp)["entries"]}
    assert files["rel1"] == "papers/a.pdf"


def test_file_import_does_not_give_the_stdin_warning(tmp_path: Path) -> None:
    cp, bp, _papers = _setup_config(str(tmp_path))
    Path(bp).write_text("")
    src = tmp_path / "export.bib"
    src.write_text(
        "@article{rel1, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1/a},\n"
        "  file = {papers/a.pdf}}\n"
    )
    result = import_from_bibtex(config_path=cp, home_dir=str(tmp_path), source_path=str(src))
    assert result["status"] == "ok", result
    assert not [w for w in result.get("warnings", []) if "stdin" in w]


def test_stdin_warning_skips_an_entry_whose_path_was_not_written(tmp_path: Path) -> None:
    """A re-import that is a duplicate stores nothing, so it is not named."""
    cp, bp, _papers = _setup_config(str(tmp_path))
    Path(bp).write_text("")
    text = (
        "@article{r1, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1000/abc},\n"
        "  file = {papers/a.pdf}}\n"
    )
    first = import_from_bibtex(
        config_path=cp, home_dir=str(tmp_path), source_path="<stdin>", source_text=text
    )
    second = import_from_bibtex(
        config_path=cp, home_dir=str(tmp_path), source_path="<stdin>", source_text=text
    )
    assert [w for w in first.get("warnings", []) if "stdin" in w]
    assert second["skipped_duplicates"] == 1
    assert not [w for w in second.get("warnings", []) if "stdin" in w], second["warnings"]


def test_stdin_warning_names_a_merge_that_filled_the_attachment(tmp_path: Path) -> None:
    """A merge that gives an existing entry its first `file` stored the path."""
    from pzi.bib_repository import read_bib_file

    cp, bp, _papers = _setup_config(str(tmp_path))
    Path(bp).write_text(
        "@article{old, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1000/aaa}}\n"
    )
    text = (
        "@article{new, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1000/aaa},\n"
        "  file = {papers/a.pdf}}\n"
    )
    result = import_from_bibtex(
        config_path=cp, home_dir=str(tmp_path), source_path="<stdin>", source_text=text
    )
    files = {e["citekey"]: e["fields"].get("file") for e in read_bib_file(bp)["entries"]}
    assert files.get("old") == "papers/a.pdf", files
    stdin_warnings = [w for w in result.get("warnings", []) if "stdin" in w]
    assert len(stdin_warnings) == 1 and "old" in stdin_warnings[0], result.get("warnings")


def _not_merged(result: dict) -> list[str]:
    return [w for w in result.get("warnings", []) if "not merged" in w]


@pytest.mark.parametrize("dry_run", [True, False])
def test_the_left_out_warning_sees_an_entry_inserted_earlier_in_the_batch(
    dry_run: bool,
) -> None:
    """The preview warns exactly as the real run does.

    The warning used to be built by re-reading the library after the batch, so
    a dry run could not see an entry inserted earlier in the same batch and
    said nothing where the real run warned.
    """
    with tempfile.TemporaryDirectory() as td:
        first, second = _two_source_pdfs(td)
        cp, bp, _pd = _setup_config(td)
        Path(bp).write_text("")
        src = os.path.join(td, "src", "export.bib")
        Path(src).write_text(
            "@article{dual, title = {Two}, author = {Doe, Jane}, year = {2020},\n"
            "  doi = {10.1000/dual}, file = {" + first + "}}\n"
            "@article{again, title = {Two}, author = {Doe, Jane}, year = {2020},\n"
            "  doi = {10.1000/dual}, file = {" + f"{first};{second}" + "}}\n"
        )

        result = import_from_bibtex(
            config_path=cp, home_dir=td, source_path=src, dry_run=dry_run
        )

        assert _not_merged(result) == [
            f"dual: kept the library entry's own file field; not merged from the "
            f"source: {second}"
        ], result


def test_a_stdin_merge_that_stored_the_relative_path_is_not_left_out(
    tmp_path: Path,
) -> None:
    """The merge stored `papers/a.pdf`; saying it was not merged is false.

    A stdin path is kept as written, so the library reads it against its own
    folder. The warning resolved the source's copy against the current
    directory instead, and the two never matched.
    """
    cp, bp, _papers = _setup_config(str(tmp_path))
    Path(bp).write_text(
        "@article{old, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1000/aaa}}\n"
    )
    text = (
        "@article{new, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1000/aaa},\n"
        "  file = {papers/a.pdf}}\n"
    )

    result = import_from_bibtex(
        config_path=cp, home_dir=str(tmp_path), source_path="<stdin>", source_text=text
    )

    assert result.get("updated") == 1, result
    assert _not_merged(result) == [], result["warnings"]


def test_a_stdin_duplicate_naming_the_same_relative_path_gives_no_warning(
    tmp_path: Path,
) -> None:
    """A plain duplicate: the entry already names the path the source offers."""
    cp, bp, _papers = _setup_config(str(tmp_path))
    entry = (
        "@article{old, title = {A}, author = {Doe, J}, year = {2020}, doi = {10.1000/aaa},\n"
        "  file = {papers/a.pdf}}\n"
    )
    Path(bp).write_text(entry)

    result = import_from_bibtex(
        config_path=cp, home_dir=str(tmp_path), source_path="<stdin>", source_text=entry
    )

    assert result["skipped_duplicates"] == 1, result
    assert _not_merged(result) == [], result["warnings"]


def test_a_missing_dotdot_attachment_keeps_a_symlinked_home_spelling(tmp_path: Path) -> None:
    """The file is not there yet, so `samefile` could not vouch for the lexical path.

    The kernel spelling was used instead, which expands a symlinked HOME and
    puts the path outside `~`, losing the configured home style.
    """
    from pzi.import_service import _resolve_like_the_kernel

    real = tmp_path / "realhome"
    (real / "src").mkdir(parents=True)
    (real / "pp").mkdir()
    home = tmp_path / "home"
    home.symlink_to(real)

    resolved = _resolve_like_the_kernel(str(home / "src"), "../pp/missing.pdf")

    assert resolved == str(home / "pp" / "missing.pdf")
