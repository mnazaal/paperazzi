from pathlib import Path

import pytest

from pzi.errors import PziError
from pzi.fileio import read_text_utf8


def test_read_text_utf8_returns_contents(tmp_path: Path) -> None:
    path = tmp_path / "ok.txt"
    path.write_text("café\n", encoding="utf-8")
    assert read_text_utf8(path) == "café\n"


def test_read_text_utf8_names_file_on_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.bib"
    path.write_bytes(b"caf\xe9")  # 0xe9 is not valid UTF-8
    with pytest.raises(PziError, match=rf"{path} is not valid UTF-8 text"):
        read_text_utf8(path)
