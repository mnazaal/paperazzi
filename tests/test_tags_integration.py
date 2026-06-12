"""Integration tests for tag_service using temp BibTeX files.

Covers list_tags, add_tags, remove_tags with real file I/O.
"""

from pathlib import Path

from pzi.bib_repository import read_bib_file
from pzi.tag_service import add_tags, list_tags, remove_tags


def _write_config_and_bib(tmp_path: Path, bib_content: str) -> Path:
    """Create a temp config.toml and .bib file, return config path."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(bib_content)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname="test"\npath="{bib_path}"\ndefault=true\n'
    )
    return config_path


VALID_BIB = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
}
"""


# --- list_tags ---


def test_list_tags_for_entry(tmp_path: Path) -> None:
    bib = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = {ml, graphs},
}
"""
    config = _write_config_and_bib(tmp_path, bib)
    result = list_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
    )
    assert result["status"] == "ok"
    assert set(result["tags"]) == {"graphs", "ml"}


def test_list_tags_all_entries(tmp_path: Path) -> None:
    bib = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = {ml},
}
@article{jones2025nets,
  title = {Neural Nets},
  author = {Jones, K},
  year = {2025},
  keywords = {dl},
}
"""
    config = _write_config_and_bib(tmp_path, bib)
    result = list_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert result["status"] == "ok"
    assert result["citekey"] is None
    assert set(result["tags"]) == {"dl", "ml"}


def test_list_tags_citekey_not_found(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = list_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
    )
    assert result["status"] == "error"
    assert "not found" in result["errors"][0]


def test_list_tags_entry_without_tags_field(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = list_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
    )
    assert result["status"] == "ok"
    assert result["tags"] == []


# --- add_tags ---


def test_add_tags_to_existing_entry(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = add_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["NLP", "Machine Learning"],
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert result["dry_run"] is False
    assert set(result["tags"]) == {"machine-learning", "nlp"}

    # Verify BibTeX file was updated
    records = read_bib_file(str(tmp_path / "test.bib"))["records"]
    assert records[0]["citekey"] == "smith2024graph"
    assert set(records[0].get("tags", [])) == {"machine-learning", "nlp"}


def test_add_tags_citekey_not_found(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = add_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
        tags=["ml"],
    )
    assert result["status"] == "error"
    assert "not found" in result["errors"][0]


def test_add_tags_empty_tags_rejected(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = add_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["!!!"],
    )
    assert result["status"] == "error"
    assert "no valid tags" in result["errors"][0]


def test_add_tags_dry_run_does_not_write(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = add_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["ml"],
        dry_run=True,
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert result["dry_run"] is True

    # Verify BibTeX file was NOT updated
    records = read_bib_file(str(tmp_path / "test.bib"))["records"]
    assert "tags" not in records[0] or not records[0].get("tags")


def test_add_tags_duplicates_noop(tmp_path: Path) -> None:
    bib = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = {ml},
}
"""
    config = _write_config_and_bib(tmp_path, bib)
    result = add_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["ml"],
    )
    assert result["status"] == "ok"
    assert result["changed"] is False
    assert "no changes" in result["message"]


# --- remove_tags ---


def test_remove_tags_from_entry(tmp_path: Path) -> None:
    bib = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = { ml , nlp , graphs },
}
"""
    config = _write_config_and_bib(tmp_path, bib)
    result = remove_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["nlp"],
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert result["tags"] == ["graphs", "ml"]

    # Verify file was updated
    records = read_bib_file(str(tmp_path / "test.bib"))["records"]
    assert set(records[0].get("tags", [])) == {"graphs", "ml"}


def test_remove_tags_citekey_not_found(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = remove_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
        tags=["ml"],
    )
    assert result["status"] == "error"
    assert "not found" in result["errors"][0]


def test_remove_tags_dry_run(tmp_path: Path) -> None:
    bib = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = {ml},
}
"""
    config = _write_config_and_bib(tmp_path, bib)
    result = remove_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["ml"],
        dry_run=True,
    )
    assert result["status"] == "ok"
    assert result["changed"] is True
    assert result["dry_run"] is True

    # Verify file NOT updated
    records = read_bib_file(str(tmp_path / "test.bib"))["records"]
    assert set(records[0].get("tags", [])) == {"ml"}


def test_remove_nonexistent_tags_noop(tmp_path: Path) -> None:
    bib = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = {ml},
}
"""
    config = _write_config_and_bib(tmp_path, bib)
    result = remove_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="smith2024graph",
        tags=["nlp"],
    )
    assert result["status"] == "ok"
    assert result["changed"] is False
    assert "no changes" in result["message"]
