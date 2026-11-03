"""`update` applies many enrichments with one parse of the library, not one each.

Every applied update used to be its own `update_bib_entry`: lock, parse the whole
library, validate it, render it, write it. On a 22,000-entry library that is
about 3.5 s per entry, so ten enrichments cost ten parses. The writes now go
through one `batch_write_session` per chunk, and the network lookups stay
outside any bib lock.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from pzi import bib_repository, update_service
from pzi.bib_repository import delete_bib_entry, update_bib_entry
from pzi.bibtex import apply_record_to_entry
from pzi.update_service import update_bib

_PRELUDE = (
    '@string{acm = "Association for Computing Machinery"}\n\n'
    "@comment{kept verbatim}\n\n"
)


def _entry(i: int, *, complete: bool) -> str:
    extra = "" if not complete else "  journal = {Done Journal},\n  year = {2019},\n"
    return (
        f"@article{{key{i:03d},\n"
        f"  author = {{Author{i}, A.}},\n"
        f"  title = {{Paper number {i} about graphs}},\n"
        f"  volume = {{{i}}},\n"
        f"  publisher = acm,\n"
        f"{extra}"
        f"  note = {{kept {i}}}\n"
        f"}}\n"
    )


def _library(tmp_path: Path, n: int, *, complete: set[int]) -> tuple[Path, Path]:
    bib = tmp_path / "ml.bib"
    bib.write_text(
        _PRELUDE + "\n".join(_entry(i, complete=i in complete) for i in range(n))
    )
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    return bib, config


def _search_for(titles: set[str]):
    def search(query: str, *, server_url: str) -> list[dict[str, Any]]:
        if query not in titles:
            return []
        return [{"record": {"title": query, "venue": "ICML", "year": 2021}}]

    return search


def _run(config: Path, tmp_path: Path, search, *, dry_run: bool) -> dict[str, Any]:
    return dict(
        update_bib(
            config_path=str(config),
            home_dir=str(tmp_path),
            bib_selector=None,
            dry_run=dry_run,
            fetch_search=search,
        )
    )


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls = [0]
    real = bib_repository.parse_bib_library

    def counting(*args: Any, **kwargs: Any):
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(bib_repository, "parse_bib_library", counting)
    return calls


def test_many_applied_updates_parse_the_library_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = set(range(0, 20, 2))
    bib, config = _library(tmp_path, 20, complete=set())
    # Every entry lacks venue/year, ten of them have a lookup result.
    search = _search_for({f"Paper number {i} about graphs" for i in targets})
    calls = _count_parses(monkeypatch)

    result = _run(config, tmp_path, search, dry_run=False)

    assert [i["citekey"] for i in result["items"]] == [f"key{i:03d}" for i in sorted(targets)]
    assert all(i["applied"] for i in result["items"])
    assert calls[0] == 1, f"{calls[0]} parses for {len(targets)} updates"
    assert bib.read_text().count("venue") == 0  # BibTeX spells it journal
    assert bib.read_text().count("ICML") == len(targets)


def test_parses_scale_with_chunks_not_with_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    n = update_service._WRITE_BATCH * 2 + 5
    bib, config = _library(tmp_path, n, complete=set())
    search = _search_for({f"Paper number {i} about graphs" for i in range(n)})
    calls = _count_parses(monkeypatch)

    result = _run(config, tmp_path, search, dry_run=False)

    assert len(result["items"]) == n
    assert calls[0] == math.ceil(n / update_service._WRITE_BATCH)
    assert bib.read_text().count("ICML") == n


def test_batched_write_is_byte_identical_to_sequential_updates(
    tmp_path: Path,
) -> None:
    """Oracle: apply the same enrichment one `update_bib_entry` at a time."""
    targets = [1, 4, 5, 9]
    bib, config = _library(tmp_path, 12, complete=set())
    reference = tmp_path / "reference.bib"
    reference.write_text(bib.read_text())
    search = _search_for({f"Paper number {i} about graphs" for i in targets})

    _run(config, tmp_path, search, dry_run=False)

    for i in targets:
        update_bib_entry(
            str(reference),
            f"key{i:03d}",
            lambda entry, record: apply_record_to_entry(
                entry, {**record, "venue": "ICML", "year": 2021}
            ),
        )
    assert bib.read_text() == reference.read_text()


def test_batched_write_keeps_everything_it_did_not_touch(tmp_path: Path) -> None:
    bib, config = _library(tmp_path, 8, complete=set())
    before = bib.read_text()
    search = _search_for({"Paper number 3 about graphs"})

    _run(config, tmp_path, search, dry_run=False)

    after = bib.read_text()
    assert after.startswith(_PRELUDE)
    for i in range(8):
        if i != 3:
            assert _entry(i, complete=False) in before
            block = before[before.index(f"@article{{key{i:03d}") :].split("\n}\n")[0]
            assert block in after
    # Fields the record model does not carry survive on the entry that changed.
    changed = after[after.index("@article{key003") :].split("\n}\n")[0]
    assert "volume = {3}" in changed
    assert "publisher = acm" in changed
    assert "note = {kept 3}" in changed


def test_dry_run_and_write_report_the_same_fields_and_diff(tmp_path: Path) -> None:
    bib, config = _library(tmp_path, 6, complete=set())
    search = _search_for({"Paper number 2 about graphs", "Paper number 4 about graphs"})
    before = bib.read_text()

    preview = _run(config, tmp_path, search, dry_run=True)
    assert bib.read_text() == before
    applied = _run(config, tmp_path, search, dry_run=False)

    assert [i["citekey"] for i in preview["items"]] == [i["citekey"] for i in applied["items"]]
    assert [i["changed_fields"] for i in preview["items"]] == [
        i["changed_fields"] for i in applied["items"]
    ]
    assert [i["applied"] for i in preview["items"]] == [False, False]
    assert [i["applied"] for i in applied["items"]] == [True, True]
    for item in preview["items"]:
        for line in item["diff"].splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                assert line[1:] in bib.read_text()


def test_unchanged_candidate_makes_no_item_and_no_write(tmp_path: Path) -> None:
    """A lookup that adds nothing new is dropped, as before."""
    bib, config = _library(tmp_path, 4, complete={0, 1, 2, 3})
    before = bib.read_text()
    # Entries are complete (venue and year present) so none is looked up.
    result = _run(config, tmp_path, _search_for({"Paper number 1 about graphs"}), dry_run=False)
    assert result["items"] == []
    assert bib.read_text() == before


def test_entry_deleted_during_lookup_is_reported_not_raised(tmp_path: Path) -> None:
    bib, config = _library(tmp_path, 5, complete=set())

    def search(query: str, *, server_url: str) -> list[dict[str, Any]]:
        if query == "Paper number 3 about graphs":
            delete_bib_entry(str(bib), "key003")
        elif query != "Paper number 1 about graphs":
            return []
        return [{"record": {"title": query, "venue": "ICML", "year": 2021}}]

    result = _run(config, tmp_path, search, dry_run=False)

    by_key = {i["citekey"]: i for i in result["items"]}
    assert by_key["key001"]["applied"] is True
    assert by_key["key003"].get("failed") is True
    assert "disappeared" in str(by_key["key003"]["note"])
    assert "ICML" in bib.read_text()


def test_one_unrepresentable_entry_fails_its_own_item_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bib, config = _library(tmp_path, 6, complete=set())
    search = _search_for({f"Paper number {i} about graphs" for i in (1, 2, 3)})
    real = update_service.validate_bibtex_roundtrip

    def refuse_key002(entries: list[Any]) -> None:
        if any(e["citekey"] == "key002" for e in entries):
            raise ValueError("cannot be written")
        real(entries)

    monkeypatch.setattr(update_service, "validate_bibtex_roundtrip", refuse_key002)

    result = _run(config, tmp_path, search, dry_run=False)

    by_key = {i["citekey"]: i for i in result["items"]}
    assert by_key["key001"]["applied"] is True
    assert by_key["key003"]["applied"] is True
    assert by_key["key002"]["applied"] is False
    assert by_key["key002"]["failed"] is True
    assert "cannot be written" in str(by_key["key002"]["note"])
    assert result["status"] == "ok"
    assert bib.read_text().count("ICML") == 2


def test_a_failed_write_session_fails_every_item_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bib, config = _library(tmp_path, 4, complete=set())
    before = bib.read_text()
    search = _search_for({"Paper number 0 about graphs", "Paper number 1 about graphs"})

    def broken(*args: Any, **kwargs: Any):
        raise OSError("disk full")

    monkeypatch.setattr(update_service, "batch_write_session", broken)

    result = _run(config, tmp_path, search, dry_run=False)

    assert result["status"] == "error"
    assert [i["failed"] for i in result["items"]] == [True, True]
    assert all("disk full" in str(i["note"]) for i in result["items"])
    assert bib.read_text() == before
