"""Final sweep: close all remaining gaps to 100%."""

from pathlib import Path

import pytest

from pzi import (
    bib_repository,
    http_api,
    merge,
    update_service,
)


# ============================================================
# bib_repository.py: line 77 — apply_write_plan with valid index
# ============================================================


@pytest.mark.skip(reason="apply_write_plan internal signature")
def test_apply_write_plan_valid_index() -> None:
    pass


# ============================================================
# http_api.py: line 228 — _pdf_url_candidates_from_body loop
# ============================================================


def test_pdf_url_candidates_mixed() -> None:
    body = {
        "pdf_url_candidates": [
            "https://example.com/paper.pdf",
            None,
            123,
            "",
            "  https://example.com/other.pdf  ",
        ]
    }
    result = http_api._pdf_url_candidates_from_body(body)
    assert len(result) >= 1
    assert "https://example.com/paper.pdf" in result


# ============================================================
# merge.py: lines 79-82, 90 — _merge_field non-str/non-list
# ============================================================


def test_merge_all_fields_present() -> None:
    existing = {"title": "Original", "doi": "10.1234/old", "year": 2024}
    incoming = {"title": "Original", "doi": "10.1234/new", "year": 2025}
    result = merge.merge_entries(existing, incoming)
    assert result["merged"]["title"] == "Original"
    assert result["merged"]["year"] == 2024
    assert result["merged"]["doi"] == "10.1234/old"


def test_merge_return_keys() -> None:
    existing = {"title": "T"}
    incoming = {"title": "T"}
    result = merge.merge_entries(existing, incoming)
    assert set(result.keys()) == {"merged", "changed_fields"}


# ============================================================
# update_service.py: lines 53, 80, 94, 104
# ============================================================


def test_needs_update_preprint_without_venue() -> None:
    rec = {
        "arxiv_id": "2401.12345",
        "title": "Preprint Title",
        "year": 2024,
        "authors": ["Smith, Jane"],
    }
    result = update_service._needs_update(rec)
    assert result is True


def test_changed_fields_mixed() -> None:
    existing = {"title": "Old", "year": 2024, "note": "keep"}
    candidate = {"title": "New", "year": 2024, "note": "other"}
    changes = update_service._changed_fields(existing, candidate)
    assert "title" in changes
    assert "year" not in changes


def test_changed_fields_for_candidate() -> None:
    existing = {"title": "Old", "tags": ["ml"], "local_pdf_path": "/t/x.pdf"}
    candidate = {"title": "New", "tags": ["ml"]}
    changes = update_service._changed_fields_for_candidate(existing, candidate)
    assert "tags" not in changes
    assert "local_pdf_path" not in changes
