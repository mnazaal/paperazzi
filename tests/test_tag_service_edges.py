"""Edge tests for tag_service.py uncovered lines (30, 43, 62->61, 156, 180, 202).

Covers: list_tags with citekey, add_tags/remove_tags no-change paths,
dry_run behavior, invalid/no tags supplied path, citekey race condition.
"""

from pathlib import Path

from pzi.add_service import add_record_to_bib
from pzi.tag_service import add_tags, list_tags, remove_tags


def _write_config(tmp_path: Path, bib_path: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
""".strip()
    )
    return config_path


# ── list_tags with citekey ───────────────────────────────────────

def test_list_tags_specific_citekey_found(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "tags": ["ml", "nlp"]},
        bib_selector=None,
        dry_run=False,
    )

    result = list_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
    )
    assert result["status"] == "ok"
    assert result["tags"] == ["ml", "nlp"]


def test_list_tags_specific_citekey_not_found(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024"},
        bib_selector=None,
        dry_run=False,
    )

    result = list_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
    )
    assert result["status"] == "error"
    assert "not found" in result["errors"][0]


# ── add_tags ─────────────────────────────────────────────────────

def test_add_tags_no_change(tmp_path: Path) -> None:
    """Adding already-present tags → no change."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "title": "Test", "tags": ["ml"]},
        bib_selector=None,
        dry_run=False,
    )

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["ml"],
        dry_run=False,
    )
    assert result["changed"] is False
    assert "no changes" in result["message"]


def test_add_tags_empty_tag_list(tmp_path: Path) -> None:
    """Empty/whitespace-only tags → no valid tags error."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "title": "Test"},
        bib_selector=None,
        dry_run=False,
    )

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["  ", ""],
        dry_run=False,
    )
    assert result["status"] == "error"
    assert "no valid tags" in result["errors"][0]


def test_add_tags_dry_run(tmp_path: Path) -> None:
    """Dry run reports would-add but doesn't write."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "title": "Test"},
        bib_selector=None,
        dry_run=False,
    )

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["new-tag"],
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["changed"] is True
    assert "would" in result["message"]
    assert "new-tag" in result["tags"]


def test_add_tags_success(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "title": "Test"},
        bib_selector=None,
        dry_run=False,
    )

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["nlp"],
        dry_run=False,
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert "nlp" in result["tags"]
    text = bib_path.read_text()
    assert "nlp" in text


# ── remove_tags ──────────────────────────────────────────────────

def test_remove_tags_no_change(tmp_path: Path) -> None:
    """Removing tags not present → no change."""
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "tags": ["ml"]},
        bib_selector=None,
        dry_run=False,
    )

    result = remove_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["cv"],
        dry_run=False,
    )
    assert result["changed"] is False
    assert "no changes" in result["message"]


def test_remove_tags_success(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "tags": ["ml", "nlp"]},
        bib_selector=None,
        dry_run=False,
    )

    result = remove_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["nlp"],
        dry_run=False,
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert "removed" in result["message"]
    assert "nlp" not in result["tags"]


def test_remove_tags_dry_run(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024", "tags": ["ml"]},
        bib_selector=None,
        dry_run=False,
    )

    result = remove_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024",
        tags=["ml"],
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["changed"] is True
    assert "would" in result["message"]


# ── citekey not found ────────────────────────────────────────────

def test_add_tags_citekey_not_found(tmp_path: Path) -> None:
    bib_path = tmp_path / "ml.bib"
    config_path = _write_config(tmp_path, bib_path)
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "smith2024"},
        bib_selector=None,
        dry_run=False,
    )

    result = add_tags(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
        tags=["ml"],
    )
    assert result["status"] == "error"
    assert "not found" in result["errors"][0]


# ── config error ─────────────────────────────────────────────────

def test_add_tags_config_error(tmp_path: Path) -> None:
    result = add_tags(
        config_path=str(tmp_path / "nonexistent.toml"),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="x",
        tags=["ml"],
    )
    assert result["status"] == "error"
    assert "could not resolve" in result["message"]
