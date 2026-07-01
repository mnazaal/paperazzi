#!/usr/bin/env python3
"""Build browser extension packages for Firefox and Chrome.

Generates browser-specific manifests from manifest.base.json and copies
shared extension files into dist/firefox/ and dist/chrome/.

Usage:
  python tools/build_extension.py

Outputs:
  dist/firefox/   — unpacked extension for Firefox (load in about:debugging)
  dist/chrome/    — unpacked extension for Chrome (load in chrome://extensions)
  dist/paperazzi-capture-firefox.zip
  dist/paperazzi-capture-chrome.zip
"""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "browser-extension"
DIST_DIR = PROJECT_ROOT / "dist"

FIREFOX_ID = "paperazzi-capture@paperazzi.local"


def _load_base_manifest() -> dict[str, Any]:
    path = SRC_DIR / "manifest.base.json"
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        sys.exit(1)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_project_version(path: Path | None = None) -> str:
    pyproject_path = PROJECT_ROOT / "pyproject.toml" if path is None else path
    if not pyproject_path.exists():
        print(f"error: {pyproject_path} not found", file=sys.stderr)
        sys.exit(1)
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        print(f"error: {pyproject_path} missing [project].version", file=sys.stderr)
        sys.exit(1)
    return version


def _manifest_with_version(base: dict[str, Any], version: str) -> dict[str, Any]:
    manifest = dict(base)
    manifest["version"] = version
    return manifest


def _build_firefox_manifest(base: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(base)
    manifest["background"] = {
        "scripts": ["background.js"],
        "type": "module",
    }
    manifest["browser_specific_settings"] = {
        "gecko": {
            "id": FIREFOX_ID,
            "strict_min_version": "109.0",
        }
    }
    # Firefox-only: webRequestFilterResponse needed for responseHeaders access in MV3
    permissions = list(manifest.get("permissions", []))
    if "webRequestFilterResponse" not in permissions:
        permissions.append("webRequestFilterResponse")
    manifest["permissions"] = permissions
    return manifest


def _build_chrome_manifest(base: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(base)
    manifest["background"] = {
        "service_worker": "background.js",
        "type": "module",
    }
    return manifest


def _copy_extension_files(dest: Path) -> None:
    EXCLUDE = frozenset({"manifest.base.json", "README.md"})
    dest.mkdir(parents=True, exist_ok=True)
    for item in SRC_DIR.iterdir():
        if item.name in EXCLUDE:
            continue
        if item.name.startswith("."):
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def _write_manifest(dest: Path, manifest: dict[str, Any]) -> None:
    path = dest / "manifest.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def _zip_directory(src: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src))


def main() -> int:
    base = _manifest_with_version(_load_base_manifest(), _load_project_version())

    firefox_dir = DIST_DIR / "firefox"
    chrome_dir = DIST_DIR / "chrome"

    # Clean previous builds
    for d in (firefox_dir, chrome_dir):
        if d.exists():
            shutil.rmtree(d)

    # Build Firefox
    _copy_extension_files(firefox_dir)
    _write_manifest(firefox_dir, _build_firefox_manifest(base))

    # Build Chrome
    _copy_extension_files(chrome_dir)
    _write_manifest(chrome_dir, _build_chrome_manifest(base))

    # Create zip packages
    _zip_directory(firefox_dir, DIST_DIR / "paperazzi-capture-firefox.zip")
    _zip_directory(chrome_dir, DIST_DIR / "paperazzi-capture-chrome.zip")

    print(f"Built {firefox_dir}")
    print(f"Built {chrome_dir}")
    print(f"Created {DIST_DIR / 'paperazzi-capture-firefox.zip'}")
    print(f"Created {DIST_DIR / 'paperazzi-capture-chrome.zip'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
