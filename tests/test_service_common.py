from __future__ import annotations

from pathlib import Path

from pzi.config import AppConfig
from pzi.service_common import load_and_resolve_bib


def _dummy_config(tmp_path: Path, default_bib_name: str = "main") -> tuple[str, AppConfig]:
    """Create a minimal config and write it to a temp path."""
    config_data = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "{default_bib_name}"
path = "/some/path/ml.bib"
papers_dir = "/some/path/papers"
default = true
"""
    config_path = tmp_path / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_data)
    return str(config_path), {"bibs": [], "dummy": True}  # type: ignore[dict-item]


def test_load_and_resolve_bib_success(tmp_path: Path) -> None:
    bib_path = tmp_path / "test.bib"
    bib_path.write_text("")

    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib_path}"
papers_dir = "{tmp_path}/papers"
default = true
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    result = load_and_resolve_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, tuple)
    config, bib = result
    assert bib["name"] == "main"
    assert bib["path"] == str(bib_path)


def test_load_and_resolve_bib_missing_config_returns_errors(tmp_path: Path) -> None:
    result = load_and_resolve_bib(
        config_path=str(tmp_path / "nonexistent.toml"),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, list)
    assert len(result) > 0


def test_load_and_resolve_bib_ambiguous_selection_returns_errors(tmp_path: Path) -> None:
    bib1 = tmp_path / "test1.bib"
    bib2 = tmp_path / "test2.bib"
    bib1.write_text("")
    bib2.write_text("")

    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib1}"
papers_dir = "{tmp_path}/papers1"

[[bibs]]
name = "alt"
path = "{bib2}"
papers_dir = "{tmp_path}/papers2"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    result = load_and_resolve_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, list)


def test_load_and_resolve_bib_by_name(tmp_path: Path) -> None:
    bib1 = tmp_path / "test1.bib"
    bib2 = tmp_path / "test2.bib"
    bib1.write_text("")
    bib2.write_text("")

    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib1}"
papers_dir = "{tmp_path}/papers1"

[[bibs]]
name = "alt"
path = "{bib2}"
papers_dir = "{tmp_path}/papers2"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)

    result = load_and_resolve_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector="alt",
    )
    assert isinstance(result, tuple)
    config, bib = result
    assert bib["name"] == "alt"


def test_load_and_resolve_bib_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("not toml at all\n=invalid")

    result = load_and_resolve_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
    )
    assert isinstance(result, list)
