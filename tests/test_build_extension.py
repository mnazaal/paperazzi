import json
import zipfile
from pathlib import Path

from tools.build_extension import (
    _build_chrome_manifest,
    _build_firefox_manifest,
    _copy_extension_files,
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
    assert manifest["browser_specific_settings"]["gecko"]["id"] == "paperazzi-capture@paperazzi.local"


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


def test_copy_extension_files_excludes_readme(tmp_path: Path) -> None:
    src = tmp_path / "browser-extension"
    src.mkdir()
    (src / "background.js").write_text("// bg")
    (src / "popup.html").write_text("<!-- popup -->")
    (src / "README.md").write_text("# internal docs")
    (src / "manifest.base.json").write_text("{}")

    dest = tmp_path / "dist" / "firefox"

    import tools.build_extension as be
    orig = be.SRC_DIR
    be.SRC_DIR = src
    try:
        _copy_extension_files(dest)
    finally:
        be.SRC_DIR = orig

    copied = sorted(p.name for p in dest.iterdir())
    assert "background.js" in copied
    assert "popup.html" in copied
    assert "README.md" not in copied
    assert "manifest.base.json" not in copied


def test_firefox_manifest_has_webrequestfilterresponse() -> None:
    base = {"permissions": ["webRequest"], "version": "1.0"}
    manifest = _build_firefox_manifest(base)
    assert "webRequestFilterResponse" in manifest["permissions"]
    assert "webRequest" in manifest["permissions"]


def test_chrome_manifest_does_not_have_webrequestfilterresponse() -> None:
    base = {"permissions": ["webRequest"], "version": "1.0"}
    manifest = _build_chrome_manifest(base)
    assert "webRequestFilterResponse" not in manifest["permissions"]
    assert "webRequest" in manifest["permissions"]


# ---------------------------------------------------------------------------
# Extension version translation
# ---------------------------------------------------------------------------


def test_extension_version_translates_pep440_prereleases() -> None:
    """Browsers accept only dot-separated integers, and ordering must hold.

    `0.1.0b2` shipped verbatim in the v0.1.0b2 zips and both Chrome and AMO
    reject it. The translation also has to keep a pre-release *below* its final
    release, which rules out simply appending the pre-release number.
    """
    from tools.build_extension import extension_version

    assert extension_version("0.1.0") == "0.1.0.9999"
    assert extension_version("0.1.0a1") == "0.1.0.1001"
    assert extension_version("0.1.0b2") == "0.1.0.2002"
    assert extension_version("0.1.0rc1") == "0.1.0.3001"
    assert extension_version("1.2.3") == "1.2.3.9999"


def test_extension_version_orders_prereleases_before_the_final_release() -> None:
    from tools.build_extension import extension_version

    def parts(v: str) -> list[int]:
        return [int(p) for p in extension_version(v).split(".")]

    assert parts("0.1.0a1") < parts("0.1.0b1") < parts("0.1.0b2")
    assert parts("0.1.0b2") < parts("0.1.0rc1") < parts("0.1.0")
    assert parts("0.1.0") < parts("0.1.1a1")


def test_extension_version_is_all_integers_within_browser_limits() -> None:
    from tools.build_extension import extension_version

    for raw in ("0.1.0", "0.1.0b2", "9.9.9rc99"):
        components = extension_version(raw).split(".")
        assert len(components) <= 4
        for component in components:
            assert component.isdigit()
            assert 0 <= int(component) <= 65535


def test_manifest_carries_numeric_version_and_human_version_name() -> None:
    from tools.build_extension import _manifest_with_version

    manifest = _manifest_with_version({"name": "pzi"}, "0.1.0b3")

    assert manifest["version"] == "0.1.0.2003"
    # The PEP 440 string is preserved for humans, not used as the version.
    assert manifest["version_name"] == "0.1.0b3"
