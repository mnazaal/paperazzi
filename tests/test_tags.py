from pathlib import Path

from pzi.errors import REASON_NOT_FOUND
from pzi.tag_service import add_tags, list_tags, normalize_tag, normalize_tags, parse_tag_csv


def _write_config_and_bib(tmp_path: Path) -> Path:
    """Create a temp config.toml + one-entry .bib, return the config path."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        "@article{smith2024graph,\n"
        "  title = {Graph Parsers},\n"
        "  author = {Smith, J},\n"
        "  year = {2024},\n"
        "}\n"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname="test"\npath="{bib_path}"\ndefault=true\n'
    )
    return config_path


def test_normalize_tag_lowercases_and_slugifies() -> None:
    assert normalize_tag("Machine Learning") == "machine-learning"


def test_normalize_tag_strips_punctuation() -> None:
    assert normalize_tag("graphs, trees & parsing!") == "graphs-trees-parsing"


def test_normalize_tag_transliterates_unicode() -> None:
    assert normalize_tag("Café") == "cafe"


def test_normalize_tag_rejects_empty_result() -> None:
    assert normalize_tag("!!!") is None


def test_normalize_tags_deduplicates_and_sorts() -> None:
    assert normalize_tags(["ML", "machine learning", "ml", " Machine-Learning "]) == [
        "machine-learning",
        "ml",
    ]


def test_parse_tag_csv_normalizes_multiple_values() -> None:
    assert parse_tag_csv("NLP, machine learning, NLP ,, graphs ") == [
        "graphs",
        "machine-learning",
        "nlp",
    ]


def test_list_tags_unknown_citekey_uses_reason_not_found_constant(tmp_path: Path) -> None:
    """`reason` must be `pzi.errors.REASON_NOT_FOUND`, not a locally hardcoded
    `"not_found"` string — the vocabulary lives in one place so it can only
    drift by changing the shared constant, not per call site.
    """
    config = _write_config_and_bib(tmp_path)
    result = list_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
    )
    assert result["status"] == "error"
    assert result["reason"] == REASON_NOT_FOUND


def test_add_tags_unknown_citekey_uses_reason_not_found_constant(tmp_path: Path) -> None:
    config = _write_config_and_bib(tmp_path)
    result = add_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nonexistent",
        tags=["ml"],
    )
    assert result["status"] == "error"
    assert result["reason"] == REASON_NOT_FOUND
