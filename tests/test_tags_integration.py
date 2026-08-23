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


def test_list_tags_reports_a_library_the_config_does_not_declare(tmp_path: Path) -> None:
    """The plain config-broken path, which was hidden behind a false pragma.

    It was marked `# pragma: no cover — covered by integration/browser tests`;
    the browser-marked files import `browser_pdf_hook` and `browser_session`
    and nothing else, so nothing covered it. Reaching it takes one wrong
    `--library`.
    """
    config = _write_config_and_bib(tmp_path, VALID_BIB)
    result = list_tags(
        config_path=str(config),
        home_dir=str(tmp_path),
        bib_selector="nosuchlibrary",
        citekey=None,
    )
    assert result["status"] == "error"
    assert result["reason"] == "config"
    assert result["tags"] == []
    assert result["errors"]


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
    # Regression: dry-run message was ungrammatical ("would added tags").
    assert result["message"] == "would add tags"

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
    assert result["message"] == "would remove tags"

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


# --- Tags the user did not write as slugs ---


NON_SLUG_BIB = """@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, J},
  year = {2024},
  keywords = {Machine Learning, Graph Nets},
}
"""


def test_remove_matches_a_stored_tag_written_in_any_spelling(tmp_path: Path) -> None:
    """`--tags` is normalized on the way in and stored tags were not compared.

    A library tag written `Machine Learning` could not be removed by any input:
    the normalized `machine-learning` never equalled the stored string, so the
    command reported "no changes" and exited 0 forever.
    """
    config_path = _write_config_and_bib(tmp_path, NON_SLUG_BIB)

    result = remove_tags(
        config_path=str(config_path), home_dir=str(tmp_path),
        citekey="smith2024graph", tags=["Machine Learning"], bib_selector=None,
    )

    assert result["status"] == "ok"
    assert result["changed"] is True
    assert result["tags"] == ["Graph Nets"]


def test_adding_a_tag_already_stored_in_another_spelling_is_a_noop(
    tmp_path: Path,
) -> None:
    """Otherwise the entry ends up carrying both spellings of one tag."""
    config_path = _write_config_and_bib(tmp_path, NON_SLUG_BIB)

    result = add_tags(
        config_path=str(config_path), home_dir=str(tmp_path),
        citekey="smith2024graph", tags=["machine-learning"], bib_selector=None,
    )

    assert result["changed"] is False
    assert result["tags"] == ["Graph Nets", "Machine Learning"]


def test_adding_a_tag_keeps_the_stored_spelling_of_the_others(
    tmp_path: Path,
) -> None:
    """Comparing normalized forms must not rewrite tags the user typed."""
    config_path = _write_config_and_bib(tmp_path, NON_SLUG_BIB)

    result = add_tags(
        config_path=str(config_path), home_dir=str(tmp_path),
        citekey="smith2024graph", tags=["to-read"], bib_selector=None,
    )

    assert result["tags"] == ["Graph Nets", "Machine Learning", "to-read"]
    assert "Machine Learning" in (tmp_path / "test.bib").read_text()


def test_a_tag_added_between_the_read_and_the_lock_is_not_lost(
    tmp_path: Path, monkeypatch
) -> None:
    """The tag set was computed from the pre-lock snapshot and written verbatim.

    Two `pzi tag add` runs racing on one entry therefore ended with only the
    second one's tag: the loser's write went through, reported success, and
    silently dropped what the winner had just added.
    """
    import pzi.tag_service as tag_service

    config_path = _write_config_and_bib(tmp_path, NON_SLUG_BIB)
    real_read = tag_service.read_bib_file

    def _stale_read(path):
        """What the entry looked like before the other writer's tag landed."""
        result = real_read(path)
        for record in result["records"]:
            if record.get("citekey") == "smith2024graph":
                record["tags"] = ["Graph Nets"]
        return result

    monkeypatch.setattr(tag_service, "read_bib_file", _stale_read)

    add_tags(
        config_path=str(config_path), home_dir=str(tmp_path),
        citekey="smith2024graph", tags=["to-read"], bib_selector=None,
    )

    written = read_bib_file(str(tmp_path / "test.bib"))
    assert written["records"][0]["tags"] == ["Graph Nets", "Machine Learning", "to-read"]
