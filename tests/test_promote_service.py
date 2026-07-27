import pzi.promote_service as promote_service
from pzi.add_service import add_record_to_bib
from pzi.promote_service import _score_published_candidate, promote_bib


def test_candidate_scoring_matches_authors_across_name_formats() -> None:
    # "Smith, John" (preprint) vs "John Smith" (candidate) must count as an
    # author match — family-name normalized, not exact-string.
    preprint = {"title": "Graph Nets", "authors": ["Smith, John"], "year": 2024}
    candidate = {"title": "Graph Nets", "authors": ["John Smith"], "year": 2024}
    same_format = {"title": "Graph Nets", "authors": ["Smith, John"], "year": 2024}

    assert _score_published_candidate(preprint, candidate) == _score_published_candidate(
        preprint, same_format
    )
    # No author overlap scores strictly lower than a full author overlap.
    no_overlap = {"title": "Graph Nets", "authors": ["Jane Doe"], "year": 2024}
    assert _score_published_candidate(preprint, candidate) > _score_published_candidate(
        preprint, no_overlap
    )


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
        keep_preprint=False,
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


def test_promote_mark_resolved_tags_and_skips_on_rerun(tmp_path):
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    first = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        mark_resolved=True,
        fetch_search=_fake_search_with_venue,
    )
    assert first["summary"]["created"] == 1
    assert first["summary"]["marked_resolved"] == 1
    assert "promoted" in bib_path.read_text()  # preprint now carries the marker tag

    # Re-running skips the already-tagged preprint instead of re-promoting it.
    second = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        mark_resolved=True,
        fetch_search=_fake_search_with_venue,
    )
    assert second["summary"]["checked"] == 0
    assert second["summary"]["skipped_already_resolved"] == 1
    assert second["summary"]["created"] == 0


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
    assert "@article{" in text
    assert "@unpublished{" in text
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
        return {"title": "Graph Parsers", "venue": "Journal of Parsing", "year": 2024}, None

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        keep_preprint=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
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
        keep_preprint=False,
        fetch_search=fake_search,
        # Above the extra-author/off-by-one-year penalty, below the default bar:
        # a threshold of 2 would pass no matter how the scoring drifted.
        confidence_threshold=40,
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


# --- S2 error differentiation tests ---


def test_promote_s2_http_429_rate_limit_no_key(tmp_path):
    """HTTP 429 from S2 with no key configured → rate-limited message."""
    from urllib.error import HTTPError

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    class _FakeResp:
        def read(self): return b""
        def close(self): pass

    def fake_s2(title):
        raise HTTPError("http://s2", 429, "Too Many Requests", {}, _FakeResp())

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (rate-limited — configure" in note


def test_promote_s2_http_403_with_key(tmp_path):
    """HTTP 403 from S2 with key configured → check-key message."""
    from urllib.error import HTTPError

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, semantic_scholar_api_key="my-key")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    class _FakeResp:
        def read(self): return b""
        def close(self): pass

    def fake_s2(title):
        raise HTTPError("http://s2", 403, "Forbidden", {}, _FakeResp())

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (rate-limited — check API key" in note


def test_promote_s2_http_500_generic(tmp_path):
    """HTTP 500 from S2 → generic HTTP error message."""
    from urllib.error import HTTPError

    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    class _FakeResp:
        def read(self): return b""
        def close(self): pass

    def fake_s2(title):
        raise HTTPError("http://s2", 500, "Server Error", {}, _FakeResp())

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (HTTP 500)" in note


def test_promote_s2_data_error_rate_limit_no_key(tmp_path):
    """S2 returns (None, 'Rate limit exceeded') with no key → rate-limited msg."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_s2(title):
        return None, "Rate limit exceeded"

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (rate-limited — configure" in note


def test_promote_s2_data_error_auth_with_key(tmp_path):
    """S2 returns (None, 'Authorization required') with key → auth msg."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path, semantic_scholar_api_key="my-key")
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_s2(title):
        return None, "Authorization required"

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["action"] == "skip"
    note = result["items"][0]["note"]
    assert "semantic-scholar (auth required)" in note


def test_promote_s2_summary_warning_multiple_rate_limits(tmp_path):
    """Two records with S2 rate-limit → s2_warning in summary."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)
    _seed_bib_with_preprint(
        tmp_path, bib_path, config_path,
        citekey="jones2024graphs2",
        title="Graph Analyzers",
        arxiv_id="2401.99999",
    )

    def fake_s2(title):
        return None, "Rate limit exceeded"

    result = promote_bib(
        config_path=str(config_path), home_dir=str(tmp_path),
        bib_selector=None, dry_run=False,
        fetch_search=lambda q, **kw: [],
        fetch_crossref=lambda t: None,
        fetch_openalex=lambda t: None,
        fetch_dblp=lambda t: None,
        fetch_openreview=lambda t: None,
        fetch_s2=fake_s2,
    )
    assert result["status"] == "ok"
    assert "s2_warning" in result["summary"]
    assert "2 Semantic Scholar rate-limit failures" in result["summary"]["s2_warning"]


def test_promote_unexpected_error_isolated_per_record(tmp_path, monkeypatch):
    """A handler blowing up on one preprint must not abort the whole run.

    The loop's per-record guard turns the failure into an explainable skip
    (counted in ``summary['skipped_failed']``) and lets later preprints promote.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    for ck, arxiv, title in [
        ("alpha2024", "2401.00001", "Alpha Net"),
        ("beta2024", "2401.00002", "Beta Net"),
    ]:
        add_record_to_bib(
            config_path=str(config_path),
            home_dir=str(tmp_path),
            record={
                "citekey": ck,
                "title": title,
                "arxiv_id": arxiv,
                "year": 2024,
                "authors": ["Smith, Jane"],
            },
            bib_selector=None,
            dry_run=False,
        )

    def _search(query: str, *, server_url: str):
        title, doi = ("Alpha Net", "10.9/alpha") if "Alpha" in query else ("Beta Net", "10.9/beta")
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": title,
                    "venue": "Journal of Parsing",
                    "doi": doi,
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    original = promote_service._handle_update_in_place

    def _flaky(*, preprint_record, **kwargs):
        if preprint_record.get("citekey") == "alpha2024":
            raise RuntimeError("kaboom")
        return original(preprint_record=preprint_record, **kwargs)

    monkeypatch.setattr(promote_service, "_handle_update_in_place", _flaky)

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_search,
    )

    assert result["status"] == "ok"
    items = {it["preprint_citekey"]: it for it in result["items"]}
    assert "promotion failed" in items["alpha2024"]["note"]
    assert result["summary"]["skipped_failed"] == 1
    # The second preprint still promoted despite the first record raising.
    assert items["beta2024"]["action"] == "update"
    assert "doi = {10.9/beta}" in bib_path.read_text()


# === destructive write paths (2026-07 audit, PLAN.md step 3) ===


def _preprint_bib_text(extra_fields: str = "") -> str:
    """A raw @unpublished preprint carrying fields the record model does not
    model, so a rewrite-from-scratch is visible as field loss."""
    return (
        "@unpublished{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "  volume = {12},\n"
        "  pages = {3--14},\n"
        "  publisher = {Cold Spring Press},\n"
        f"{extra_fields}"
        "}\n"
    )


def test_promote_keep_preprint_inserts_instead_of_rewriting_the_preprint(tmp_path):
    """Keep-mode must insert the published version, never patch the preprint.

    The merged published record inherits the preprint's ``url`` — no provider
    normalizer emits ``canonical_url`` — so an identity-matched write plan turns
    the intended insert into an in-place update of the preprint itself, and the
    reported ``published_citekey`` ends up existing nowhere in the file.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path,
        bib_path,
        config_path,
        canonical_url="https://arxiv.org/abs/2401.12345",
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    item = result["items"][0]
    assert item["action"] == "create"
    published_ck = item["published_citekey"]
    assert published_ck != "smith2024graph"

    text = bib_path.read_text()
    # The published citekey the run reported must actually be in the file...
    assert f"{{{published_ck}," in text
    # ...and the preprint must still be the preprint: unpublished, and not
    # carrying the published venue/doi it never had.
    assert "@unpublished{smith2024graph," in text
    assert text.count("journal = {Journal of Parsing}") == 1
    assert text.count("doi = {10.9/jop}") == 1


def test_promote_rejects_a_candidate_matching_on_authors_alone(tmp_path):
    """Shared surnames alone must never clear the promotion gate.

    The gate scores on a coarse integer scale where three shared surnames hit
    the default threshold exactly, so a candidate with an unrelated title is
    written in — while the 0-100 ``score_match`` breakdown printed alongside it
    reports a title mismatch.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(
        tmp_path,
        bib_path,
        config_path,
        authors=["Smith, Jane", "Doe, John", "Roe, Ann"],
    )

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "An Entirely Unrelated Study of Beetles",
                    "venue": "Journal of Coleoptera",
                    "doi": "10.9/beetle",
                    "year": 2019,
                    "authors": ["Smith, Jane", "Doe, John", "Roe, Ann"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["items"][0]["action"] == "skip"
    assert "low confidence" in result["items"][0]["note"]
    assert "10.9/beetle" not in bib_path.read_text()


def test_promote_applies_the_configured_default_threshold(tmp_path):
    """With no threshold in config, the gate is 60 — the loader's default.

    `promote_service` used to carry its own fallback of 3, left over from the
    pre-`score_match` feature-count scale. It was unreachable (`AppConfig` is a
    total TypedDict, so the loader always supplies the key), but a re-introduced
    independent default would reopen the gate to near-anything.

    The candidate below scores exactly 50: same title, disjoint authors, which
    `score_match` flags `chimeric` — the classic fabricated-author citation.
    50 is above 3 and below 60, so it is promoted under the stale fallback and
    skipped under the real default. That band is the point of this test.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)  # no promote_confidence_threshold
    _seed_bib_with_preprint(tmp_path, bib_path, config_path)

    def fake_search(query: str, *, server_url: str):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "doi": "10.9/jop",
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
    )

    assert result["items"][0]["action"] == "skip"
    assert result["items"][0]["note"] == "low confidence (50 < 60)"
    assert "10.9/jop" not in bib_path.read_text(), "nothing may be written"


def test_promote_does_not_blank_fields_the_candidate_left_none(tmp_path):
    """A candidate key explicitly set to ``None`` must not clear a populated
    field — ``_openreview_normalize`` always emits ``doi: None``."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    _seed_bib_with_preprint(tmp_path, bib_path, config_path, doi="10.1/preprint")

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Graph Parsers",
                    "venue": "Journal of Parsing",
                    "doi": None,
                    "year": 2024,
                    "authors": ["Smith, Jane"],
                },
                "attachments": [],
            }
        ]

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=fake_search,
    )

    assert result["items"][0]["action"] == "update"
    text = bib_path.read_text()
    assert "doi = {10.1/preprint}" in text
    assert "journal = {Journal of Parsing}" in text


def test_promote_update_in_place_preserves_unmodelled_fields(tmp_path):
    """Promoting in place rewrites the entry from the record projection, which
    drops every BibTeX field the record model does not carry."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(_preprint_bib_text())

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "update"
    text = bib_path.read_text()
    assert "volume = {12}" in text
    assert "pages = {3--14}" in text
    assert "publisher = {Cold Spring Press}" in text
    assert "journal = {Journal of Parsing}" in text
    # The promoted entry is no longer unpublished; its type is resolved from the
    # published record rather than hardcoded.
    assert "@article{smith2024graph," in text


def test_promote_keep_preprint_note_preserves_unmodelled_preprint_fields(tmp_path):
    """Stamping the cross-reference note onto the preprint must not rewrite the
    rest of its entry away."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(_preprint_bib_text())

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "create"
    text = bib_path.read_text()
    assert "Published version:" in text
    assert "Preprint version:" in text
    assert "volume = {12}" in text
    assert "pages = {3--14}" in text
    assert "publisher = {Cold Spring Press}" in text


def test_promote_in_place_moves_the_venue_when_it_retypes_the_entry(tmp_path):
    """Retyping an entry must move its venue to the key the new type expects.

    `merge_projected_entry` writes the venue back to whichever key the entry
    already used — right for an ordinary update, wrong when promotion retypes a
    proceedings entry to `@article`, which would leave the journal name sitting
    in `booktitle`.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        "@inproceedings{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  booktitle = {Workshop on Parsing},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n"
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=False,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "update"
    text = bib_path.read_text()
    assert "@article{smith2024graph," in text
    assert "journal = {Journal of Parsing}" in text
    assert "booktitle" not in text


def test_promote_keep_preprint_note_preserves_a_booktitle_venue(tmp_path):
    """The preprint's note update must not move or drop its venue.

    A preprint filed as `@inproceedings` keeps its venue in `booktitle`. The
    projection round-trips venue through the record's single `venue` key, so a
    note update that merges twice reads back `journal` (absent), concludes the
    venue was cleared, and deletes `booktitle`.
    """
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        "@inproceedings{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, Jane},\n"
        "  year = {2024},\n"
        "  booktitle = {Workshop on Parsing},\n"
        "  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n"
        "}\n"
    )

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["items"][0]["action"] == "create"
    text = bib_path.read_text()
    assert "booktitle = {Workshop on Parsing}" in text


def test_promote_keep_preprint_writes_nothing_when_library_is_malformed(tmp_path):
    """The insert half of keep-mode commits before the note updates run, and
    only the note path refuses to patch a malformed library — so a broken block
    anywhere in the file leaves a committed entry behind a reported failure."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    bib_path.write_text(
        _preprint_bib_text()
        + "\n@article{broken2024,\n"
        "  title = {Missing closing brace\n"
        "  author = {Someone},\n"
        "  year = {2024},\n"
        "}\n"
    )
    before = bib_path.read_text()

    result = promote_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        keep_preprint=True,
        dry_run=False,
        fetch_search=_fake_search_with_venue,
    )

    assert result["summary"]["created"] == 0
    assert bib_path.read_text() == before
