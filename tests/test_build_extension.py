import json
import zipfile
from pathlib import Path

from tools.build_extension import (
    _build_chrome_manifest,
    _build_firefox_manifest,
    _load_base_manifest,
    _zip_directory,
)


def test_load_base_manifest_reads_file(tmp_path: Path) -> None:
    src = tmp_path / "browser-extension"
    src.mkdir()
    manifest = {"name": "test", "version": "1.0"}
    (src / "manifest.base.json").write_text(json.dumps(manifest))

    import tools.build_extension as be
    orig = be.SRC_DIR
    be.SRC_DIR = src
    try:
        result = _load_base_manifest()
        assert result == manifest
    finally:
        be.SRC_DIR = orig


def test_firefox_manifest_includes_scripts_and_gecko_id() -> None:
    base = {"name": "x", "version": "1.0"}
    manifest = _build_firefox_manifest(base)
    assert manifest["background"] == {
        "scripts": ["background.js"],
        "type": "module",
    }
    assert manifest["browser_specific_settings"]["gecko"]["id"] == "pzi-capture@pzi.local"


def test_chrome_manifest_uses_service_worker() -> None:
    base = {"name": "x", "version": "1.0"}
    manifest = _build_chrome_manifest(base)
    assert manifest["background"] == {
        "service_worker": "background.js",
        "type": "module",
    }
    assert "browser_specific_settings" not in manifest


def test_zip_directory_creates_valid_zip(tmp_path: Path) -> None:
    src = tmp_path / "ext"
    src.mkdir()
    (src / "manifest.json").write_text("{}")
    nested = src / "js"
    nested.mkdir()
    (nested / "bg.js").write_text("// bg")

    zip_path = tmp_path / "out.zip"
    _zip_directory(src, zip_path)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "js/bg.js" in names
