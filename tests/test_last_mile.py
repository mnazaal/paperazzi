"""Last-mile coverage: targeted tests for final remaining lines."""

from pathlib import Path

from pzi import update_service

# === update_service.py: 53, 80, 94, 104 ===


def test_update_bib_skips_records_without_citekey(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{,\n  title={No Citekey}\n}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_search(query, *, server_url):
        return []

    result = update_service.update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"
    # No items because no record had a citekey
    assert len(result["items"]) == 0


def test_update_bib_no_changed_fields(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{test2024,\n"
        "  title={Same Title},\n"
        "  doi={10.1234/test},\n"
        "  year={2024}\n"
        "}\n"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_search(query, *, server_url):
        return [
            {
                "item_type": "journalArticle",
                "record": {
                    "title": "Same Title",
                    "doi": "10.1234/test",
                    "year": 2024,
                    "venue": "Journal of Stuff",
                    "authors": ["Smith, Jane"],
                },
            }
        ]

    result = update_service.update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"


def test_update_record_without_query(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{test2024,\n  title={},\n}\n")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )

    def fake_search(query, *, server_url):
        return []

    result = update_service.update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_search,
    )
    assert result["status"] == "ok"
    # No query string → record skipped
    assert len(result["items"]) == 0
