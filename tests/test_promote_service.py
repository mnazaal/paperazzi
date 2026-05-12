from pzi.add_service import add_record_to_bib
from pzi.promote_service import promote_bib


def _seed_bib_with_preprint(tmp_path, bib_path, config_path, **kwargs):
    record = {
        "citekey": "smith2024graph",
        "title": "Graph Parsers",
        "arxiv_id": "2401.12345",
        "year": 2024,
        "authors": ["Smith, Jane"],
        **kwargs,
    }
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record=record,
        bib_selector=None,
        dry_run=False,
    )


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


def _fake_search_with_venue(query: str, *, server_url: str):
    return [
        {
            "item_type": "journalArticle",
            "record": {
                "title": "Graph Parsers",
                "venue": "Journal of Parsing",
                "doi": "10.9/jop",
                "year": 2024,
                "authors": ["Smith, Jane"],
                "pdf_url": "https://example.com/paper.pdf",
            },
            "attachments": [],
        }
    ]


def _fake_search_no_venue(query: str, *, server_url: str):
    return [
        {
            "item_type": "preprint",
            "record": {
                "title": "Graph Parsers",
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            "attachments": [],
        }
    ]


def test_promote_dry_run_does_not_write(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    before = bib_path.read_text()

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=_fake_search_with_venue,
    )

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "update"
    assert bib_path.read_text() == before


def test_promote_update_in_place(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["action"] == "update"
    assert item["published_citekey"] == "smith2024graph"
    text = bib_path.read_text()
    assert "journal = {Journal of Parsing}" in text
    assert "doi = {10.9/jop}" in text


def test_promote_keep_preprint_creates_new_entry(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["action"] == "create"
    assert item["published_citekey"] is not None
    assert item["published_citekey"] != "smith2024graph"

    text = bib_path.read_text()
    assert text.count("@article{") == 2
    assert "journal = {Journal of Parsing}" in text
    assert "Published version:" in text
    assert "Preprint version:" in text


def test_promote_skips_when_published_already_exists(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "smith2024graph_jop",
            "title": "Graph Parsers",
            "venue": "Journal of Parsing",
            "doi": "10.9/jop",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    assert "already exists" in result["items"][0]["note"]


def test_promote_skips_low_confidence(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Totally Different Title",
                    "venue": "Journal of X",
                    "year": 2024,
                    "authors": ["Doe, John"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
        confidence_threshold=3,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    assert "low confidence" in result["items"][0]["note"]


def test_promote_skips_non_preprints(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "doe2024vision",
            "title": "Vision",
            "venue": "CVPR",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert len(result["items"]) == 0


def test_promote_attaches_pdf_when_available(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_fetch_binary(url):
        return b"%PDF-1.4 test", "application/pdf"

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["pdf_attached"] is True


def test_promote_pdf_failure_still_updates_metadata(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_fetch_binary(url):
        raise ConnectionError("no")

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["action"] == "update"
    assert item["pdf_attached"] is False
    text = bib_path.read_text()
    assert "journal = {Journal of Parsing}" in text


# --- additional coverage tests ---


def test_promote_errors_when_bib_not_found(tmp_path):
    config_path = _write_config(tmp_path, tmp_path / "missing.bib")
    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="nonexistent",
        dry_run=False,
    )
    assert result["status"] == "error"
    assert len(result["errors"]) > 0


def test_promote_record_without_citekey_skipped(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    # Manually write a record without citekey
    bib_path.write_text("@article{},\n")

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 0


def test_promote_uses_s2_api_key(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, semantic_scholar_api_key="test-key")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    s2_calls = []

    def fake_s2(title):
        s2_calls.append(title)
        return {"title": "Graph Parsers", "venue": "Journal of Parsing", "year": 2024}

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_s2=fake_s2,
    )

    assert s2_calls == ["Graph Parsers"]
    assert result["status"] == "ok"
    assert result["items"][0]["action"] == "update"


def test_promote_empty_query_skips_search(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "empty", "title": ""},
        bib_selector=None,
        dry_run=False,
    )

    search_calls = []

    def fake_search(q, *, server_url):
        search_calls.append(q)
        return []

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert search_calls == []
    assert result["status"] == "ok"


def test_promote_different_author_year_scoring(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers Extended",
                    "venue": "Journal of Parsing",
                    "year": 2025,
                    "authors": ["Smith, Jane", "Doe, John"],
                },
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=fake_search,
        confidence_threshold=2,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "update"


def test_promote_keep_preprint_pdf_failure(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_fetch_binary(url):
        raise ConnectionError("no")

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
        fetch_binary=fake_fetch_binary,
    )

    item = result["items"][0]
    assert item["action"] == "create"
    assert item["pdf_attached"] is False


def test_promote_dry_run_keep_preprint_no_pdf(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=True,
        fetch_search=_fake_search_with_venue,
    )

    item = result["items"][0]
    assert item["action"] == "create"
    assert item["pdf_attached"] is False


def test_promote_find_duplicate_by_title(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    # Add duplicate with same title but different citekey
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={
            "citekey": "other2024graph",
            "title": "Graph Parsers",
            "venue": "Journal of Parsing",
            "year": 2024,
        },
        bib_selector=None,
        dry_run=False,
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    assert "other2024graph" in result["items"][0]["note"]
