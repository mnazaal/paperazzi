"""Stale-write safety: target identity and the action reported after a rebase."""

from __future__ import annotations

from pathlib import Path

import pytest

from pzi.bib_repository import (
    StalePlanError,
    execute_write_plan,
    plan_bib_write,
    read_bib_file,
)
from pzi.capture_local_pdf import plan_with_applied_record

PAPER_A = (
    "@article{key2020,\n  author = {Smith, Jane},\n  title = {Paper A},\n"
    "  doi = {10.1000/aaa},\n  year = {2020},\n}\n"
)
PAPER_B = (
    "@article{key2020,\n  author = {Jones, Bob},\n  title = {Paper B},\n"
    "  doi = {10.1000/bbb},\n  year = {2020},\n}\n"
)


def _incoming(doi: str, title: str, citekey: str = "key2020") -> dict:
    return {
        "citekey": citekey,
        "title": title,
        "authors": ["Smith, Jane"],
        "year": 2020,
        "doi": doi,
        "abstract": "an abstract",
    }


def test_stale_update_targeting_a_replaced_same_key_paper_is_refused(tmp_path: Path) -> None:
    bib = tmp_path / "lib.bib"
    bib.write_text(PAPER_A)
    before = read_bib_file(str(bib))
    plan = plan_bib_write(
        _incoming("10.1000/aaa", "Paper A"),  # type: ignore[arg-type]
        before["records"],
        existing_entries=before["entries"],
    )
    assert plan["action"] == "update"

    bib.write_text(PAPER_B)  # same key, different paper
    with pytest.raises(StalePlanError):
        execute_write_plan(str(bib), plan)
    assert bib.read_text() == PAPER_B


def test_concurrent_edit_to_the_same_paper_still_merges(tmp_path: Path) -> None:
    bib = tmp_path / "lib.bib"
    bib.write_text(PAPER_A)
    before = read_bib_file(str(bib))
    plan = plan_bib_write(
        _incoming("10.1000/aaa", "Paper A"),  # type: ignore[arg-type]
        before["records"],
        existing_entries=before["entries"],
    )
    bib.write_text(PAPER_A.replace("year = {2020},", "year = {2020},\n  note = {mine},"))
    execute_write_plan(str(bib), plan)
    text = bib.read_text()
    assert "note = {mine}" in text and "10.1000/aaa" in text


def test_two_writers_same_doi_agree_on_count_action_and_changed_fields(
    tmp_path: Path,
) -> None:
    bib = tmp_path / "lib.bib"
    bib.write_text("")
    record = _incoming("10.1000/same", "Same Paper", "same2020")
    empty = read_bib_file(str(bib))
    plan1 = plan_bib_write(record, empty["records"], existing_entries=empty["entries"])  # type: ignore[arg-type]
    plan2 = plan_bib_write(record, empty["records"], existing_entries=empty["entries"])  # type: ignore[arg-type]
    assert plan1["action"] == plan2["action"] == "insert"

    entries1 = execute_write_plan(str(bib), plan1)
    assert plan_with_applied_record(plan1, record, entries1)["action"] == "insert"  # type: ignore[arg-type]

    entries2 = execute_write_plan(str(bib), plan2)  # rebased into an update
    applied = plan_with_applied_record(plan2, record, entries2)  # type: ignore[arg-type]

    assert len(read_bib_file(str(bib))["entries"]) == 1
    assert len(entries2) == 1
    assert applied["action"] == "update"
    assert applied["changed_fields"] == []  # nothing new: "already captured"


def test_baseless_plan_may_change_identity_as_replace_promotion_does(
    tmp_path: Path,
) -> None:
    bib = tmp_path / "lib.bib"
    bib.write_text(PAPER_A)
    before = read_bib_file(str(bib))
    plan = plan_bib_write(
        _incoming("10.1000/aaa", "Paper A"),  # type: ignore[arg-type]
        before["records"],
        existing_entries=before["entries"],
    )
    del plan["base_entry"]  # a hand-built replace-mode plan carries no base
    plan["record"] = {**plan["record"], "doi": "10.1000/new"}
    plan["entry"] = {**plan["entry"], "fields": {**plan["entry"]["fields"], "doi": "10.1000/new"}}
    execute_write_plan(str(bib), plan)
    assert "10.1000/new" in bib.read_text()
