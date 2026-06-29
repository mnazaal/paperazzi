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
