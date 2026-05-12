"""Edge tests for bib_repository.py uncovered lines (line 77: no-op update)."""

from pathlib import Path

from pzi.bib_repository import update_bib_entry


def test_update_bib_entry_noop_when_updater_returns_same_entry(tmp_path: Path) -> None:
    """Line 77: updater returns identical entry → no write needed, found=True."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text(
        "@article{smith2024,\n"
        "  author = {John Smith},\n"
        "  title = {An Article},\n"
        "  year = {2024},\n"
        "}\n"
    )

    result = update_bib_entry(
        str(bib_path),
        "smith2024",
        lambda entry, record: dict(entry),  # returns same entry unchanged
    )
    assert result["found"] is True
    assert result["entry"] is not None
    assert result["record"] is not None


def test_update_bib_entry_not_found_returns_false(tmp_path: Path) -> None:
    """Line 68-69: citekey not found → found=False."""
    bib_path = tmp_path / "test.bib"
    bib_path.write_text("@article{other,\n  title = {X}\n}\n")

    result = update_bib_entry(
        str(bib_path),
        "smith2024",
        lambda entry, record: entry,
    )
    assert result["found"] is False
    assert result["entry"] is None
    assert result["record"] is None
