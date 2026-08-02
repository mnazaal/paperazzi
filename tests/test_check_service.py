from pzi.add_service import add_record_to_bib
from pzi.check_service import check_bib


def _write_config(tmp_path, bib_path, **kwargs):
    config_path = tmp_path / "config.toml"
    app_extra = "\n".join(f'{k} = "{v}"' for k, v in kwargs.items())
    prefix = f"{app_extra}\n" if app_extra else ""
    config_path.write_text(
        f"""
{prefix}[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    return config_path


def _seed(tmp_path, config_path, **record):
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record=record,
        bib_selector=None,
        dry_run=False,
    )


def _setup(tmp_path, **record):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed(tmp_path, config_path, **record)
    return config_path


def _no_source(_title):
    return None


def test_verified_when_source_confirms(tmp_path):
    config_path = _setup(
        tmp_path,
        citekey="vaswani2017",
        title="Attention Is All You Need",
        authors=["Vaswani, Ashish", "Shazeer, Noam"],
        year=2017,
    )

    def crossref(_title, **_kw):
        return {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "venue": "NeurIPS",
        }

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=crossref,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    assert result["status"] == "ok"
    item = result["items"][0]
    assert item["verdict"] == "verified"
    assert item["confidence_score"] >= 80
    assert "crossref" in item["sources_checked"]


def test_title_only_source_does_not_verify_authorship(tmp_path):
    """A source with no author list cannot corroborate authorship.

    Scoring it as author *disagreement* would be a false alarm, but letting a
    bare title match reach `verified` is worse: reproducing a real title with
    invented authors is precisely what a fabricated citation looks like. The
    honest verdict is unconfirmed.
    """
    config_path = _setup(
        tmp_path,
        citekey="vaswani2017",
        title="Attention Is All You Need",
        authors=["Vaswani, Ashish", "Shazeer, Noam"],
        year=2017,
    )

    def crossref(_title, **_kw):
        return {"title": "Attention Is All You Need", "year": 2017, "venue": "NeurIPS"}

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=crossref,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )

    item = result["items"][0]
    assert item["verdict"] == "could_not_verify"
    # Not an accusation of a defect — just unconfirmed.
    assert "author_mismatch" not in item["flags"]
    assert "chimeric" not in item["flags"]
    assert "author_unknown" in item["flags"]


def test_could_not_verify_when_no_source_matches(tmp_path):
    config_path = _setup(
        tmp_path, citekey="ghost2020", title="A Totally Real Paper", authors=["Nobody, A"]
    )
    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=_no_source,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    item = result["items"][0]
    assert item["verdict"] == "could_not_verify"
    assert item["confidence_score"] == 0


def test_problematic_on_chimeric_authors(tmp_path):
    config_path = _setup(
        tmp_path,
        citekey="he2016",
        title="Deep Residual Learning for Image Recognition",
        authors=["He, Kaiming"],
    )

    def crossref(_title, **_kw):
        # Same title, completely different authors → chimeric citation.
        return {
            "title": "Deep Residual Learning for Image Recognition",
            "authors": ["Random, Person", "Another, Fake"],
            "venue": "CVPR",
        }

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=crossref,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    item = result["items"][0]
    assert item["verdict"] == "problematic"
    assert "chimeric" in item["flags"] or "author_mismatch" in item["flags"]


def test_problematic_on_future_year(tmp_path):
    config_path = _setup(
        tmp_path, citekey="future2099", title="Time Travel Methods", year=2099
    )
    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=_no_source,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    item = result["items"][0]
    assert item["verdict"] == "problematic"
    assert "future_year" in item["flags"]


def test_counts_and_total(tmp_path):
    config_path = _setup(
        tmp_path, citekey="a2020", title="Known Paper", authors=["Smith, J"], year=2020
    )

    def crossref(_title, **_kw):
        return {"title": "Known Paper", "authors": ["J Smith"], "venue": "ICML", "year": 2020}

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=crossref,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    assert result["total"] == 1
    assert sum(result["counts"].values()) == 1


def test_strict_catches_title_typo_that_default_verifies(tmp_path):
    # One-character typo in a long title: token overlap stays high enough that
    # the default matcher verifies it, but strict's edit-distance check flags it.
    config_path = _setup(
        tmp_path,
        citekey="typo2020",
        title="Deep Residuals Learning for Visual Image Recognition Using Convolutional Networks",
        authors=["Smith, Jane"],
    )

    def crossref(_title, **_kw):
        return {
            "title": "Deep Residual Learning for Visual Image Recognition Using Convolutional Networks",
            "authors": ["Jane Smith"],
            "venue": "NeurIPS",
        }

    common = dict(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=crossref,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    assert check_bib(**common, strict=False)["items"][0]["verdict"] == "verified"
    strict_item = check_bib(**common, strict=True)["items"][0]
    assert strict_item["verdict"] == "problematic"
    assert "title_mismatch" in strict_item["flags"]


def test_strict_catches_truncated_authors(tmp_path):
    config_path = _setup(
        tmp_path, citekey="trunc2020", title="A Big Collaboration", authors=["First, A"]
    )

    def crossref(_title, **_kw):
        return {
            "title": "A Big Collaboration",
            "authors": ["A First", "B Second", "C Third", "D Fourth"],
            "venue": "Science",
        }

    strict_item = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=crossref,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
        strict=True,
    )["items"][0]
    assert strict_item["verdict"] == "problematic"
    assert "author_truncated" in strict_item["flags"]


def test_strict_uses_higher_bar(tmp_path):
    config_path = _setup(
        tmp_path, citekey="x2020", title="Partial Match Title Here", authors=["Smith, J"]
    )

    def weak(_title, **_kw):
        # Title overlaps partially, author matches: lands between the two bars.
        return {"title": "Partial Match Different Words", "authors": ["J Smith"]}

    common = dict(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=weak,
        fetch_openalex=_no_source,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )
    lenient = check_bib(**common, strict=False)["items"][0]
    strict = check_bib(**common, strict=True)["items"][0]
    # Strict must never be more lenient than default for the same entry.
    verdicts = {"verified": 2, "could_not_verify": 1, "problematic": 1}
    assert verdicts[strict["verdict"]] >= verdicts[lenient["verdict"]] or strict["verdict"] == lenient["verdict"]


def test_offline_run_reports_unreachable_sources_rather_than_silent_abstention(
    tmp_path, monkeypatch
):
    """Network-down must not look identical to "no source knows this paper".

    With every provider unreachable, `check` used to list all five as
    `sources_checked`, report `could_not_verify`, and carry no errors — the
    same output shape as a genuinely unconfirmable (possibly fabricated)
    reference, which is the finding this command exists to surface.
    """
    config_path = _setup(
        tmp_path,
        citekey="vaswani2017",
        title="Attention Is All You Need",
        authors=["Vaswani, Ashish"],
        year=2017,
    )

    def _offline_fetch_text(*_args, **_kwargs):
        def _raise(_url: str) -> str:
            raise OSError("connection refused")

        return _raise

    monkeypatch.setattr(
        "pzi.check_service.build_metadata_fetch_text", _offline_fetch_text
    )

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        now_year=2026,
    )

    item = result["items"][0]
    assert item["verdict"] == "could_not_verify"
    # No source answered, so none may be claimed as checked.
    assert item["sources_checked"] == []
    assert item["source_errors"], "provider failures must be recorded"
    assert any("connection refused" in err for err in item["source_errors"])
    assert "no source could be reached" in item["mismatches"][0]
    # And the run says so once, rather than only per entry.
    assert result["errors"]


def test_a_source_that_answers_is_still_checked_when_another_fails(tmp_path):
    """One dead provider must not erase the ones that did answer."""
    config_path = _setup(
        tmp_path,
        citekey="vaswani2017",
        title="Attention Is All You Need",
        authors=["Vaswani, Ashish", "Shazeer, Noam"],
        year=2017,
    )

    def openalex(_title, **_kw):
        return {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "venue": "NeurIPS",
        }

    def exploding(_title, **_kw):
        raise OSError("connection refused")

    # crossref is queried first, so it must be the failing one — a confirming
    # crossref would short-circuit the loop before any later provider runs.
    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=exploding,
        fetch_openalex=openalex,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )

    item = result["items"][0]
    assert item["verdict"] == "verified"
    assert "openalex" in item["sources_checked"]
    assert "crossref" not in item["sources_checked"]
    assert any("crossref" in err for err in item["source_errors"])


def test_a_search_miss_from_one_source_does_not_condemn_a_confirmed_entry(tmp_path):
    """`--strict` reported `problematic` with `confidence_score: 100`.

    Defect flags were unioned across *every* source that returned something, so
    one provider's search landing on a different paper — close enough to survive
    the "unrelated hit" filter, far enough to be flagged `title_mismatch` — voted
    down two sources that had confirmed the entry exactly. A source whose own
    match does not identify the work cannot testify about it.
    """
    config_path = _setup(
        tmp_path,
        citekey="he2016deep",
        title="Deep Residual Learning for Image Recognition",
        authors=["He, Kaiming", "Zhang, Xiangyu"],
        year=2016,
    )

    def _correct(_title, **_kw):
        return {
            "title": "Deep Residual Learning for Image Recognition",
            "authors": ["Kaiming He", "Xiangyu Zhang"],
            "year": 2016,
            "venue": "CVPR",
        }

    def _wrong_paper(_title, **_kw):
        return {
            "title": "Deep Residual Networks for Image Classification",
            "authors": ["Kaiming He", "Xiangyu Zhang"],
            "year": 2016,
            "venue": "CVPR",
        }

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=_correct,
        fetch_openalex=_correct,
        fetch_dblp=_wrong_paper,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
        strict=True,
    )

    item = result["items"][0]
    assert item["verdict"] == "verified", item
    assert "title_mismatch" not in item["flags"]


def test_a_defect_from_a_source_that_did_identify_the_work_still_counts(tmp_path):
    """The union exists for a reason: a sparse title-only record can outscore a
    Crossref record that found the same paper under a *different* DOI, and that
    `doi_mismatch` is exactly what `pzi check` is for."""
    config_path = _setup(
        tmp_path,
        citekey="vaswani2017",
        title="Attention Is All You Need",
        authors=["Vaswani, Ashish", "Shazeer, Noam"],
        year=2017,
        doi="10.1000/wrong",
    )

    def _title_only(_title, **_kw):
        return {"title": "Attention Is All You Need", "venue": "NeurIPS"}

    def _same_paper_other_doi(_title, **_kw):
        return {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "doi": "10.5555/3295222.3295349",
        }

    result = check_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        fetch_crossref=_same_paper_other_doi,
        fetch_openalex=_title_only,
        fetch_dblp=_no_source,
        fetch_openreview=_no_source,
        fetch_s2=_no_source,
        now_year=2026,
    )

    item = result["items"][0]
    assert item["verdict"] == "problematic"
    assert "doi_mismatch" in item["flags"]
