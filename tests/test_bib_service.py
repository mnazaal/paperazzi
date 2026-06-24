"""Tests for bib_service.py (list entries, detail, bib listing, set-default).

Targets the 27% coverage gap in bib_service.py.
"""

from __future__ import annotations

import os
from pathlib import Path

from pzi.bib_service import (
    delete_entry,
    entry_detail,
    list_bibs,
    list_entries,
)
from pzi.config import dump_app_config


def _write_config(td: str, bib_name: str = "ml") -> str:
    config_path = os.path.join(td, ".config", "pzi", "config.toml")
    bib_path = os.path.join(td, f"{bib_name}.bib")
    papers_dir = os.path.join(td, "papers")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    os.makedirs(papers_dir, exist_ok=True)
    config = {
        "bibs": [{"name": bib_name, "path": bib_path, "papers_dir": papers_dir, "default": True}],
        "translation_server_url": "http://127.0.0.1:1969",
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
    }
    Path(config_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config_path).write_text(dump_app_config(config))
    return config_path


# ── list_bibs ───────────────────────────────────────────────────────────────


def test_list_bibs_returns_configured_libraries(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    result = list_bibs(config_path=config_path, home_dir=str(tmp_path))
    assert result["status"] == "ok"
    assert len(result["bibs"]) == 1
    assert result["bibs"][0]["name"] == "ml"
    assert result["bibs"][0]["default"] is True


def test_list_bibs_errors_on_missing_config(tmp_path: Path) -> None:
    config_path = os.path.join(str(tmp_path), ".config", "pzi", "nonexistent.toml")
    result = list_bibs(config_path=config_path, home_dir=str(tmp_path))
    assert result["status"] == "error"


# ── list_entries ────────────────────────────────────────────────────────────


def test_list_entries_empty_library(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    result = list_entries(config_path=config_path, home_dir=str(tmp_path), bib_selector=None)
    assert result["status"] == "ok"
    assert result["items"] == []
    assert result["total"] == 0


def test_list_entries_with_data(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    bib_path = os.path.join(str(tmp_path), "ml.bib")
    bib_path_ref = Path(bib_path)
    bib_path_ref.write_text(
        """@article{smith2024x,
  title = {Test Paper},
  author = {Smith, Jane},
  year = {2024}
}\n"""
    )
    result = list_entries(config_path=config_path, home_dir=str(tmp_path), bib_selector=None)
    assert result["status"] == "ok"
    assert result["total"] >= 1
    keys = [item["citekey"] for item in result["items"]]
    assert "smith2024x" in keys


def test_list_entries_sort_author_handles_bibtex_author_strings(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    bib_path_ref = Path(os.path.join(str(tmp_path), "ml.bib"))
    bib_path_ref.write_text(
        """@article{zeta2024,
  title = {Zeta Paper},
  author = {Zeta, Zoe},
  year = {2024}
}

@article{alpha2024,
  title = {Alpha Paper},
  author = {Alpha, Amy},
  year = {2024}
}\n"""
    )

    result = list_entries(
        config_path=config_path,
        home_dir=str(tmp_path),
        bib_selector=None,
        sort="author",
    )

    assert result["status"] == "ok"
    assert [item["citekey"] for item in result["items"]] == ["alpha2024", "zeta2024"]


def test_list_entries_respects_pagination(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    bib_path_ref = Path(os.path.join(str(tmp_path), "ml.bib"))
    entries = ""
    for i in range(5):
        entries += f"@article{{test{i},\n  title = {{Paper {i}}},\n  year = {{2024}}\n}}\n"
    bib_path_ref.write_text(entries)
    result = list_entries(
        config_path=config_path, home_dir=str(tmp_path), bib_selector=None,
        offset=0, limit=3,
    )
    assert result["status"] == "ok"
    assert len(result["items"]) == 3
    assert result["total"] == 5


# ── entry_detail ────────────────────────────────────────────────────────────


def test_entry_detail_finds_existing(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    bib_path_ref = Path(os.path.join(str(tmp_path), "ml.bib"))
    bib_path_ref.write_text(
        """@article{smith2024x,
  title = {Test Paper},
  author = {Smith, Jane and Doe, John},
  year = {2024},
  doi = {10.1234/test.001}
}\n"""
    )
    result = entry_detail(config_path=config_path, home_dir=str(tmp_path), citekey="smith2024x")
    assert result["status"] == "ok"
    assert result["citekey"] == "smith2024x"
    rec = result["record"]
    assert rec["title"] == "Test Paper"
    assert rec["year"] == 2024
    assert rec["doi"] == "10.1234/test.001"


def test_entry_detail_not_found(tmp_path: Path) -> None:
    config_path = _write_config(str(tmp_path))
    result = entry_detail(config_path=config_path, home_dir=str(tmp_path), citekey="nonexistent")
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()


# ── delete_entry ────────────────────────────────────────────────────────────


def test_delete_entry_creates_backup_before_removing_entry(tmp_path: Path) -> None:
    _write_config(str(tmp_path))
    bib_path = Path(os.path.join(str(tmp_path), "ml.bib"))
    original = """@article{keep2024,
  title = {Keep Me},
  year = {2024}
}

@article{delete2024,
  title = {Delete Me},
  year = {2024}
}\n"""
    bib_path.write_text(original)

    result = delete_entry(bib_path=str(bib_path), citekey="delete2024", dry_run=False)

    assert result["status"] == "ok"
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert backup_path.read_text() == original
    updated_text = bib_path.read_text()
    assert "keep2024" in updated_text
    assert "delete2024" not in updated_text
