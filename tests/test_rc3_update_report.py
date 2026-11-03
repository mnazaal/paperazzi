"""`update` reports what it committed, not a path-style artefact."""
from __future__ import annotations

from pathlib import Path

from pzi import update_service


def _library(tmp_path: Path, bib_text: str) -> Path:
    home = tmp_path / "home"
    lib = home / "lib"
    (lib / "Papers").mkdir(parents=True)
    (lib / "Papers" / "r.pdf").write_bytes(b"%PDF-r")
    bib = lib / "lib.bib"
    bib.write_text(bib_text)
    cfg = home / "config.toml"
    cfg.write_text(
        'pdf_file_path_style = "relative"\n[[bibs]]\nname = "t"\n'
        f'path = "{bib}"\ndefault = true\npapers_dir = "{lib / "Papers"}"\n'
    )
    return cfg


_BASE = "@article{r2020,\n  title = {Rel paper},\n  file = {Papers/r.pdf},\n}\n"
_FILLED = (
    "@article{r2020,\n  title = {Rel paper},\n  journal = {V},\n  year = {2021},\n"
    "  file = {Papers/r.pdf},\n}\n"
)


def test_item_that_added_nothing_is_not_reported_applied(tmp_path: Path, monkeypatch) -> None:
    cfg = _library(tmp_path, _BASE)
    bib = tmp_path / "home" / "lib" / "lib.bib"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    def search(query: str, *, server_url: str):
        bib.write_text(_FILLED)  # another writer filled the same fields first
        return [{"record": {"title": query, "venue": "V", "year": 2021}}]

    result = update_service.update_bib(
        config_path=str(cfg), home_dir=str(tmp_path / "home"), bib_selector=None,
        dry_run=False, fetch_search=search,
    )
    assert bib.read_text() == _FILLED
    assert not [i for i in result["items"] if i.get("applied") or i.get("changed_fields")]


def test_applied_item_reports_only_fields_it_wrote(tmp_path: Path, monkeypatch) -> None:
    cfg = _library(tmp_path, _BASE)
    bib = tmp_path / "home" / "lib" / "lib.bib"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = update_service.update_bib(
        config_path=str(cfg), home_dir=str(tmp_path / "home"), bib_selector=None,
        dry_run=False,
        fetch_search=lambda q, *, server_url: [
            {"record": {"title": q, "venue": "V", "year": 2021}}
        ],
    )
    (item,) = result["items"]
    assert item["applied"] is True
    assert sorted(item["changed_fields"]) == ["venue", "year"]
    assert "journal = {V}" in bib.read_text()
