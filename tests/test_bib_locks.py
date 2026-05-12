from pathlib import Path

from pzi.bib_repository import with_bib_lock


def test_with_bib_lock_creates_lock_file_and_releases(tmp_path: Path) -> None:
    bib_path = tmp_path / "library.bib"
    lock_file = Path(str(bib_path) + ".lock")
    with with_bib_lock(str(bib_path)):
        assert lock_file.exists()

    with with_bib_lock(str(bib_path)):
        pass


def test_with_bib_lock_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir" / "library.bib"
    with with_bib_lock(str(nested)):
        assert nested.parent.exists()
