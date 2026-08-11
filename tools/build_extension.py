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
import re
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


# PEP 440 pre-release phase -> the block it occupies in the 4th version
# component. Ordered so alpha < beta < rc < final, and a final release sits
# above every pre-release of the same X.Y.Z.
_PRERELEASE_BLOCK = {"a": 1000, "b": 2000, "rc": 3000}
_FINAL_BLOCK = 9999

_PEP440_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")


def extension_version(project_version: str) -> str:
    """Translate a PEP 440 version into one browsers accept.

    Chrome and AMO require 1-4 dot-separated integers in 0-65535, so the
    project's ``0.1.0b2`` is rejected outright — that is what shipped in the
    v0.1.0b2 zips and made them uninstallable.

    The pre-release becomes the 4th component, in a block per phase, because
    browsers compare component-wise: a pre-release has to sort *below* its final
    release, which simply appending the pre-release number would invert
    (``0.1.0.2`` > ``0.1.0``). So ``0.1.0b2`` -> ``0.1.0.2002`` and the final
    ``0.1.0`` -> ``0.1.0.9999``.
    """
    match = _PEP440_RE.match(project_version.strip())
    if match is None:
        print(
            f"error: cannot translate version {project_version!r} into an "
            "extension version (expected X.Y.Z with an optional aN/bN/rcN)",
            file=sys.stderr,
        )
        sys.exit(1)
    major, minor, patch, phase, serial = match.groups()
    if phase is None:
        fourth = _FINAL_BLOCK
    else:
        fourth = _PRERELEASE_BLOCK[phase] + int(serial)
        if fourth >= _FINAL_BLOCK:
            print(
                f"error: pre-release number {serial} in {project_version!r} is "
                "too large to order below the final release",
                file=sys.stderr,
            )
            sys.exit(1)
    return f"{major}.{minor}.{patch}.{fourth}"


def _manifest_with_version(base: dict[str, Any], version: str) -> dict[str, Any]:
    manifest = dict(base)
    manifest["version"] = extension_version(version)
    # Keep the real project version visible to humans; browsers display this
    # when present and ignore it for update comparisons.
    manifest["version_name"] = version
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
            # 128, not 109. Two features this manifest depends on land later
            # than 109 (MDN browser-compat-data): `background.type: "module"`
            # is 112+, and `optional_host_permissions` — the entire cross-origin
            # PDF path — is 128+. So 109–111 could not load `background.js` at
            # all, and 109–127 installed cleanly and silently lost cross-origin
            # PDF fetching, which is the failure the user cannot diagnose.
            "strict_min_version": "128.0",
        }
    }
    # No `webRequestFilterResponse`. It was added here on the belief that MV3
    # needs it for `responseHeaders`, which is wrong: it gates
    # `webRequest.filterResponseData` — reading and rewriting the *body* of
    # every matching response — and the extension calls that nowhere. Reading
    # headers via `onHeadersReceived` needs only `webRequest`. Asking for it
    # bought nothing and put the strongest permission in the API on the listing
    # a reviewer reads.
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
    # The extension is AGPL like the rest of the project, and the ZIPs are
    # distributed on their own from the releases page — a copyleft artifact
    # shipped without its license is the one packaging omission that actually
    # matters.
    license_path = SRC_DIR.parent / "LICENSE"
    if license_path.is_file():
        shutil.copy2(license_path, dest / "LICENSE")


def _write_manifest(dest: Path, manifest: dict[str, Any]) -> None:
    path = dest / "manifest.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def missing_manifest_references(build_dir: Path) -> list[str]:
    """Return the files ``build_dir``'s manifest names but does not contain.

    The manifest is written independently of the copy, so a renamed or
    uncopied file produces a build that only fails when a browser tries to
    load it. Nothing checked this: the build tests assert manifest *shape*.

    Covers the keys that name a path — icons, the popup, and the background
    script under either browser's spelling. It does not follow ES module
    imports: `background.js` importing a missing module is a different failure,
    caught by the extension test suite, which executes those modules.
    """
    manifest = json.loads((build_dir / "manifest.json").read_text(encoding="utf-8"))
    referenced: list[str] = []
    referenced.extend(str(path) for path in manifest.get("icons", {}).values())
    action_icon = manifest.get("action", {}).get("default_icon")
    if isinstance(action_icon, str):
        referenced.append(action_icon)
    elif isinstance(action_icon, dict):
        referenced.extend(str(path) for path in action_icon.values())
    popup = manifest.get("action", {}).get("default_popup")
    if isinstance(popup, str):
        referenced.append(popup)
    background = manifest.get("background", {})
    if isinstance(background.get("service_worker"), str):
        referenced.append(background["service_worker"])
    referenced.extend(str(path) for path in background.get("scripts", []))

    return sorted({name for name in referenced if not (build_dir / name).is_file()})


#: Every entry is stamped with this instead of its source mtime. The value is
#: arbitrary but must be constant and >= 1980, which is the earliest a ZIP can
#: represent. Chosen as the DOS epoch so it is obviously not a real build time.
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _zip_directory(src: Path, zip_path: Path) -> None:
    """Zip *src* reproducibly: same inputs, byte-identical output.

    `rglob` yields in filesystem order and `shutil.copy2` preserves source
    mtimes, so the archive's entry order and every `ZipInfo` timestamp came
    from the checkout it was built in. Two clones of the same commit produced
    different bytes, which makes the published artifact impossible to verify
    against the source it claims to be built from.
    """
    # Sorted by the name each file is stored under, not by `Path`, which orders
    # by path parts: `background.js` and `background/capture.js` come out in
    # opposite orders under the two rules. Either is deterministic, but only
    # this one matches what `namelist()` shows a reader.
    files = sorted(
        ((p.relative_to(src).as_posix(), p) for p in src.rglob("*") if p.is_file()),
    )
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in files:
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            # `ZipInfo` built by hand defaults to 0o600, unlike `ZipFile.write`,
            # which takes the mode from the file. Left alone it would ship an
            # archive whose members only the extracting user can read.
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())


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

    # Before packaging, not after: a manifest naming a file the build did not
    # copy loads as a broken extension, and the failure surfaces in the
    # browser rather than here.
    for build_dir in (firefox_dir, chrome_dir):
        missing = missing_manifest_references(build_dir)
        if missing:
            named = ", ".join(missing)
            print(f"error: {build_dir.name} manifest references missing files: {named}")
            return 1

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
