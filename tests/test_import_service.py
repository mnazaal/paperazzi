"""Tests for pzi.import_service — bulk BibTeX import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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
        assert result["imported"] == 0  # dry run
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
        "  doi = {10.1/seed},\n"
        "  title = {Seed Paper},\n"
        "  year = {2020},\n"
        "}\n"
    )
    records: list[dict[str, object]] = [
        {"citekey": "alpha", "title": "Alpha Paper",
         "authors": ["Brown, B"], "year": 2021, "doi": "10.1/alpha"},
        {"citekey": "seeddup", "title": "Seed Paper Revised",
         "authors": ["Anderson, A"], "year": 2020, "doi": "10.1/seed"},  # dedup vs library
        {"citekey": "beta", "title": "Beta Paper",
         "authors": ["Clark, C"], "year": 2022, "doi": "10.1/beta"},
        {"citekey": "alphadup", "title": "Alpha Paper",
         "authors": ["Brown, B"], "year": 2021, "doi": "10.1/alpha"},  # dedup vs record 0
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
