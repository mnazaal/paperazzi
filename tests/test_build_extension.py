import json
import os
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


def test_neither_build_asks_for_response_body_access() -> None:
    """`webRequestFilterResponse` gates `webRequest.filterResponseData` — the
    ability to read and rewrite the *body* of every matching response. The
    extension calls it nowhere; it reads response *headers* via
    `onHeadersReceived`, which needs only `webRequest`. Asking for it bought
    nothing and put the strongest permission in the API on the Firefox listing."""
    base = {"permissions": ["webRequest"], "version": "1.0"}

    firefox = _build_firefox_manifest(base)

    assert "webRequestFilterResponse" not in firefox["permissions"]
    assert "webRequest" in firefox["permissions"]


def test_chrome_manifest_does_not_have_webrequestfilterresponse() -> None:
    base = {"permissions": ["webRequest"], "version": "1.0"}
    manifest = _build_chrome_manifest(base)
    assert "webRequestFilterResponse" not in manifest["permissions"]
    assert "webRequest" in manifest["permissions"]


# ---------------------------------------------------------------------------
# Packaging: reproducibility and what the manifest promises
# ---------------------------------------------------------------------------


def test_the_same_tree_zips_to_the_same_bytes(tmp_path: Path) -> None:
    """A published artifact nobody can reproduce cannot be checked against its
    source. Entry order came from `rglob`'s filesystem order and every
    timestamp from `shutil.copy2`'s preserved mtimes, so two clones of one
    commit produced different archives.
    """
    from tools.build_extension import _zip_directory

    def build(root: Path, mtime: float) -> bytes:
        source = root / "ext"
        (source / "background").mkdir(parents=True)
        (source / "popup.html").write_text("<html></html>")
        (source / "background" / "capture.js").write_text("export const a = 1;\n")
        (source / "manifest.json").write_text('{"manifest_version": 3}')
        for path in sorted(source.rglob("*")):
            os.utime(path, (mtime, mtime))
        archive = root / "out.zip"
        _zip_directory(source, archive)
        return archive.read_bytes()

    # Two checkouts of the same commit: identical content, different mtimes.
    first = build(tmp_path / "clone-a", mtime=1_000_000_000)
    second = build(tmp_path / "clone-b", mtime=1_700_000_000)

    assert first == second

    # And stored in the order a reader sees, which `Path` sorting would not give:
    # `background.js` and `background/capture.js` order oppositely under the two.
    with zipfile.ZipFile(tmp_path / "clone-a" / "out.zip") as zf:
        names = zf.namelist()
    assert names == sorted(names), names


def test_the_zip_members_are_readable_after_extraction(tmp_path: Path) -> None:
    """Hand-built `ZipInfo` defaults to 0o600, unlike `ZipFile.write`."""
    from tools.build_extension import _zip_directory

    source = tmp_path / "ext"
    source.mkdir()
    (source / "manifest.json").write_text("{}")
    archive = tmp_path / "out.zip"
    _zip_directory(source, archive)

    with zipfile.ZipFile(archive) as zf:
        mode = zf.getinfo("manifest.json").external_attr >> 16
    assert mode & 0o444, oct(mode)


def test_a_manifest_naming_a_missing_file_is_caught(tmp_path: Path) -> None:
    """The manifest is written independently of the copy, so a rename ships a
    build that only fails when a browser loads it. The existing tests assert
    manifest shape and would not notice.
    """
    from tools.build_extension import missing_manifest_references

    build_dir = tmp_path / "chrome"
    build_dir.mkdir()
    (build_dir / "manifest.json").write_text(
        json.dumps(
            {
                "icons": {"48": "icon48.png"},
                "action": {"default_popup": "popup.html", "default_icon": "icon48.png"},
                "background": {"service_worker": "background.js"},
            }
        )
    )
    (build_dir / "icon48.png").write_bytes(b"\x89PNG")

    missing = missing_manifest_references(build_dir)

    assert missing == ["background.js", "popup.html"], missing

    # And says nothing once the build is complete.
    (build_dir / "popup.html").write_text("<html></html>")
    (build_dir / "background.js").write_text("// entry\n")
    assert missing_manifest_references(build_dir) == []


def test_the_firefox_background_spelling_is_checked_too(tmp_path: Path) -> None:
    """Firefox uses `background.scripts`, Chrome `background.service_worker`.

    Checking only one spelling would leave the other build unguarded, which is
    exactly the asymmetry that lets a packaging bug reach one browser only.
    """
    from tools.build_extension import missing_manifest_references

    build_dir = tmp_path / "firefox"
    build_dir.mkdir()
    (build_dir / "manifest.json").write_text(
        json.dumps({"background": {"scripts": ["background.js"], "type": "module"}})
    )

    assert missing_manifest_references(build_dir) == ["background.js"]


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
