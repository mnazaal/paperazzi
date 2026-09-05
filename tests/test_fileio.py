from pathlib import Path

import pytest

from pzi.errors import PziError
from pzi.fileio import directory_folds_case, read_text_utf8


def test_read_text_utf8_returns_contents(tmp_path: Path) -> None:
    path = tmp_path / "ok.txt"
    path.write_text("café\n", encoding="utf-8")
    assert read_text_utf8(path) == "café\n"


def test_read_text_utf8_names_file_on_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.bib"
    path.write_bytes(b"caf\xe9")  # 0xe9 is not valid UTF-8
    with pytest.raises(PziError, match=rf"{path} is not valid UTF-8 text"):
        read_text_utf8(path)


# === case sensitivity is a property of the filesystem ===


def test_directory_folds_case_answers_from_an_existing_name(tmp_path: Path) -> None:
    """A populated directory is decided without writing anything.

    The `.pdf` extension is itself a cased name, so a papers directory answers
    on its first entry — which is what keeps `--dry-run` free of filesystem
    writes over a real library.
    """
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "Knuth-1984-Literate.pdf").write_bytes(b"%PDF-1.4\n")

    before = sorted(p.name for p in papers.iterdir())
    folds = directory_folds_case(papers)
    assert folds is (papers / "kNUTH-1984-lITERATE.PDF").exists()
    assert sorted(p.name for p in papers.iterdir()) == before, "the probe wrote"


def test_directory_folds_case_probes_an_empty_directory_and_cleans_up(
    tmp_path: Path,
) -> None:
    """Nothing to read from, so it writes — and leaves nothing behind."""
    empty = tmp_path / "empty"
    empty.mkdir()

    assert directory_folds_case(empty) in (True, False)
    assert list(empty.iterdir()) == []


def test_directory_folds_case_is_not_fooled_by_two_real_spellings(
    tmp_path: Path,
) -> None:
    """A case-sensitive directory may genuinely hold both spellings.

    Looking up the swapped name alone would then find a *different* file and
    read the filesystem as case-folding, so the answer is confirmed with
    `samefile`.
    """
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "Paper.pdf").write_bytes(b"%PDF-1.4\n")
    try:
        (papers / "pAPER.PDF").write_bytes(b"%PDF-1.5\n")
    except OSError:  # pragma: no cover — a folding filesystem cannot stage this
        pytest.skip("this filesystem folds case, so the two spellings are one file")
    if len(list(papers.iterdir())) != 2:
        pytest.skip("this filesystem folds case, so the two spellings are one file")

    assert directory_folds_case(papers) is False


def test_directory_folds_case_reports_case_sensitive_when_it_cannot_tell(
    tmp_path: Path,
) -> None:
    """A directory that is not there is pzi's existing behaviour, not a crash."""
    assert directory_folds_case(tmp_path / "missing") is False
