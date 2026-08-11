"""Tests for pzi.import_service — bulk BibTeX import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pzi.bib_repository import update_bib_entry
from pzi.import_service import import_from_bibtex

# We need a minimal valid config.toml for add_record_to_bib to resolve bibs.
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
