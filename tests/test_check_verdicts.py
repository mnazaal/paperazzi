"""`pzi check` is the audit tool; a false verdict is the worst bug it can have.

Two directions matter equally here: a fabricated citation reported as
`verified`, and a correct one reported as `problematic` (which under `--strict`
fails CI on a good bibliography).
"""

from __future__ import annotations

import json
from pathlib import Path

from pzi.check_service import check_bib
from pzi.resolution_match import score_match

MINIMAL_CONFIG = """
[[bibs]]
name = "main"
path = "{bib_path}"
default = true
"""


def _config(tmp_path: Path, bib_text: str) -> tuple[str, Path]:
    bib_path = tmp_path / "main.bib"
    bib_path.write_text(bib_text, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")
    return str(config_path), bib_path


# ---------------------------------------------------------------------------
# The year
# ---------------------------------------------------------------------------


def test_a_wrong_year_is_a_defect() -> None:
    """Documented in README and the module docstring since forever; nothing
    ever compared the year, so `year = {1999}` for a 2017 paper scored
    `verified, confidence 100, flags: []`."""
    match = score_match(
        {"title": "Attention Is All You Need", "authors": ["Vaswani, Ashish"], "year": 1999},
        {"title": "Attention Is All You Need", "authors": ["Vaswani, Ashish"], "year": 2017},
    )

    assert "year_mismatch" in match["flags"]
    assert match["score"] < 100


def test_a_one_year_gap_is_not_a_defect() -> None:
    """Online-first and print years legitimately differ by one."""
    match = score_match(
        {"title": "A Paper", "authors": ["Smith, Jane"], "year": 2020},
        {"title": "A Paper", "authors": ["Smith, Jane"], "year": 2021},
    )

    assert "year_mismatch" not in match["flags"]


def test_a_wrong_year_makes_the_entry_problematic(tmp_path: Path) -> None:
    config_path, _bib_path = _config(
        tmp_path,
        "@article{v1999,\n"
        "  title = {Attention Is All You Need},\n"
        "  author = {Vaswani, Ashish},\n"
        "  year = {1999},\n"
        "}\n",
    )

    def _source(title: str):
        return {
            "title": "Attention Is All You Need",
            "authors": ["Vaswani, Ashish"],
            "year": 2017,
        }

    result = check_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=_source,
    )

    item = result["items"][0]
    assert item["verdict"] == "problematic"
    assert "year_mismatch" in item["flags"]


# ---------------------------------------------------------------------------
# Correct entries must not be accused
# ---------------------------------------------------------------------------


def test_a_name_particle_does_not_look_like_a_different_author() -> None:
    """"Jan van der Berg" split to a family name of `berg`, while
    "van der Berg, Jan" — the same person — split to `van der berg`."""
    match = score_match(
        {"title": "A Paper About Things", "authors": ["Jan van der Berg"]},
        {"title": "A Paper About Things", "authors": ["van der Berg, Jan"]},
    )

    assert match["author_similarity"] == 100
    assert "chimeric" not in match["flags"]
    assert "author_mismatch" not in match["flags"]


def test_a_transliterated_umlaut_is_the_same_author() -> None:
    match = score_match(
        {"title": "A Paper About Things", "authors": ["Mueller, Hans"]},
        {"title": "A Paper About Things", "authors": ["Müller, Hans"]},
    )

    assert match["author_similarity"] == 100
    assert "author_mismatch" not in match["flags"]


def test_an_abbreviated_author_list_is_confirmed_not_contradicted() -> None:
    """An entry listing 2 of 4 authors is an ordinary citation, not a defect;
    symmetric Jaccard scored it 50 and reported `problematic`."""
    match = score_match(
        {"title": "A Paper", "authors": ["Smith, Jane", "Doe, John"]},
        {
            "title": "A Paper",
            "authors": ["Smith, Jane", "Doe, John", "Roe, Ann", "Poe, Ed"],
        },
    )

    assert match["author_similarity"] == 100
    assert "author_mismatch" not in match["flags"]


def test_an_author_the_source_does_not_have_still_lowers_the_score() -> None:
    match = score_match(
        {"title": "A Paper", "authors": ["Smith, Jane", "Fabricated, Nobody"]},
        {"title": "A Paper", "authors": ["Smith, Jane"]},
    )

    assert match["author_similarity"] == 50


def test_an_unindexed_paper_is_could_not_verify_not_problematic(
    tmp_path: Path,
) -> None:
    """A by-title search returns its top hit whatever it is, so a workshop paper
    a source does not index came back as some *other* paper and the tool
    accused a genuine citation of being fabricated."""
    config_path, _bib_path = _config(
        tmp_path,
        "@inproceedings{workshop2023,\n"
        "  title = {A Very Specific Workshop Paper On Widgets},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2023},\n"
        "}\n",
    )

    def _unrelated_top_hit(title: str):
        return {
            "title": "Completely Different Research About Molecular Biology",
            "authors": ["Other, Person"],
            "year": 2010,
        }

    result = check_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=_unrelated_top_hit,
    )

    item = result["items"][0]
    assert item["verdict"] == "could_not_verify"
    assert "title_mismatch" not in item["flags"]


# ---------------------------------------------------------------------------
# Evidence must not be suppressed
# ---------------------------------------------------------------------------


def test_a_defect_from_a_lower_scoring_source_is_not_suppressed(
    tmp_path: Path,
) -> None:
    """A sparse title-only record that happened to score higher hid a Crossref
    record's `doi_mismatch` — the exact signal the command exists to raise."""
    config_path, _bib_path = _config(
        tmp_path,
        "@article{a1,\n"
        "  title = {A Paper About Things},\n"
        "  author = {Smith, Jane},\n"
        "  doi = {10.1234/claimed},\n"
        "  year = {2020},\n"
        "}\n",
    )

    def _crossref(title: str):
        return {
            "title": "A Paper About Things",
            "authors": ["Smith, Jane"],
            "doi": "10.1234/actual",
            "year": 2020,
        }

    def _openalex(title: str):
        # Sparser, but scores higher: no DOI to disagree about.
        return {"title": "A Paper About Things", "authors": ["Smith, Jane"], "year": 2020}

    result = check_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        bib_selector=None,
        strict=True,
        fetch_crossref=_crossref,
        fetch_openalex=_openalex,
    )

    item = result["items"][0]
    assert "doi_mismatch" in item["flags"]
    assert item["verdict"] == "problematic"


def test_entries_the_parser_dropped_are_reported_not_ignored(tmp_path: Path) -> None:
    """A 3-entry bib with a duplicate citekey audited as `total: 1,
    verified: 1, problematic: 0, status: ok` — an unaudited entry inside a
    clean bill of health.

    The first fix for that set `status: "error"`, which the runner reads as
    "could not run": it discarded the completed audit, wrote no report and
    exited 5. The requirement was never that the audit be thrown away, only
    that it not read as clean — so the notice moved to the `warnings` channel
    the other read commands use, and the run exits `FINDINGS`.
    """
    config_path, _bib_path = _config(
        tmp_path,
        "@article{shared,\n  title = {Real Paper},\n  author = {Smith, Jane},\n"
        "  year = {2020},\n}\n\n"
        "@article{shared,\n  title = {Fabricated Paper},\n  author = {Nobody, No},\n"
        "  year = {2021},\n}\n",
    )

    result = check_bib(
        config_path=config_path,
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=lambda title: None,
    )

    assert any("not audited" in warning for warning in result["warnings"])
    # The audit it *did* complete survives, rather than being discarded.
    assert result["status"] == "ok"
    assert result["total"] == 1


# ---------------------------------------------------------------------------
# The report file is the artifact CI archives
# ---------------------------------------------------------------------------


def test_the_report_file_records_the_same_status_as_the_run(tmp_path: Path) -> None:
    from pzi.commands.check import run_check_command

    report = tmp_path / "report.json"

    report_path = str(report)

    class _Args:
        strict = False
        json = False
        jsonl = None
        report = report_path

    def _check_bib(**_kwargs):
        return {
            "status": "ok",
            "bib_name": "main",
            "strict": False,
            "total": 1,
            "counts": {"verified": 0, "could_not_verify": 1, "problematic": 0},
            "items": [{"citekey": "a1", "verdict": "could_not_verify",
                       "confidence_score": 0, "flags": [], "mismatches": [],
                       "sources_checked": [], "source_errors": ["crossref: refused"]}],
            "errors": ["crossref: unreachable for some or all entries"],
        }

    import io

    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_check_command(
        _Args(),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
        check_bib_fn=_check_bib,
    )

    assert code != 0
    assert json.loads(report.read_text(encoding="utf-8"))["status"] == "error"
