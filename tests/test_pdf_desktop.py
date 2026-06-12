from pathlib import Path

from pzi.pdf import _desktop_browser_timeout, _wait_for_stable_file


def test_desktop_browser_timeout_defaults_and_clamps() -> None:
    assert _desktop_browser_timeout(None) == 300
    assert _desktop_browser_timeout("5") == 30
    assert _desktop_browser_timeout("bad") == 300
    assert _desktop_browser_timeout("120") == 120


def test_wait_for_stable_file_returns_true_for_unchanged_file(tmp_path: Path) -> None:
    target = tmp_path / "paper.pdf"
    target.write_bytes(b"%PDF-stable")

    assert _wait_for_stable_file(target, stable_seconds=0.01)


def test_wait_for_stable_file_returns_false_for_missing_file(tmp_path: Path) -> None:
    assert not _wait_for_stable_file(tmp_path / "missing.pdf", stable_seconds=0.01)
