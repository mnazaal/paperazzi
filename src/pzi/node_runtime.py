"""Portable Node.js runtime bootstrap.

Pure logic: detect a system Node.js, or download and extract a portable build
into the pzi data home.  Independent of the Zotero translation-server — the only
coupling is that :func:`ensure_node` returns the node binary path that
``ts_backend`` then feeds to the translation-server install.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import TextIO
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_MIN_NODE_MAJOR = 22


def _is_loopback_host(host: str | None) -> bool:
    """Return True when *host* names the local machine."""
    if not host:
        return False
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _node_mirror() -> str:
    """Return the configured Node.js mirror, rejecting insecure schemes.

    Defaults to the official HTTPS dist server.  ``PZI_NODE_MIRROR`` may
    override it, but a plain ``http://`` mirror is only honoured for a loopback
    host (local dev mirror) — otherwise the download could be silently
    downgraded to an unauthenticated transport, defeating the checksum check.
    """
    mirror = os.environ.get("PZI_NODE_MIRROR", "https://nodejs.org/dist")
    parts = urlsplit(mirror)
    if parts.scheme == "https":
        return mirror
    if parts.scheme == "http" and _is_loopback_host(parts.hostname):
        return mirror
    raise RuntimeError(
        f"refusing insecure PZI_NODE_MIRROR {mirror!r}: use https:// "
        "(http:// is allowed only for a loopback host)"
    )


def _expected_node_sha256(*, mirror: str, version: str, tarball_name: str) -> str:
    """Return the published sha256 for *tarball_name* from SHASUMS256.txt."""
    url = f"{mirror}/v{version}/SHASUMS256.txt"
    try:
        with urlopen(Request(url, method="GET"), timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except (URLError, OSError) as exc:
        raise RuntimeError(f"failed to fetch Node.js checksums from {url}: {exc}") from exc
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == tarball_name:
            return fields[0].lower()
    raise RuntimeError(f"no checksum for {tarball_name} in {url}")


def detect_node(min_version: tuple[int, int] = (_MIN_NODE_MAJOR, 0)) -> str | None:
    """Return path to system Node.js binary if it meets min_version, else None."""
    node = shutil.which("node")
    if node is None:
        return None
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    version_str = result.stdout.strip().lstrip("v")
    try:
        parts = version_str.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if (major, minor) < min_version:
        return None
    return node


def _node_dist_name() -> str:
    """Map sys.platform + machine to the Node.js dist suffix."""
    plat = sys.platform
    arch = platform.machine()
    if plat == "linux":
        plat_name = "linux"
    elif plat == "darwin":
        plat_name = "darwin"
    else:
        raise RuntimeError(f"unsupported platform for portable Node.js: {plat}")

    if arch in ("x86_64", "amd64"):
        arch_name = "x64"
    elif arch in ("aarch64", "arm64"):
        arch_name = "arm64"
    else:
        raise RuntimeError(f"unsupported architecture for portable Node.js: {arch}")

    return f"{plat_name}-{arch_name}"


def _latest_node_version() -> str:
    """Return the latest v{_MIN_NODE_MAJOR}.x version string from the index."""
    mirror = _node_mirror()
    index_url = f"{mirror}/index.json"
    try:
        with urlopen(Request(index_url, method="GET"), timeout=15) as resp:
            import json

            data = json.loads(resp.read())
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"failed to fetch Node.js version index: {exc}") from exc

    for entry in data:
        version: str | None = entry.get("version")
        if not isinstance(version, str):
            continue
        stripped = version.lstrip("v")
        try:
            major = int(stripped.split(".")[0])
        except (ValueError, IndexError):
            continue
        if major == _MIN_NODE_MAJOR:
            return stripped
    raise RuntimeError(f"no Node.js v{_MIN_NODE_MAJOR}.x found in {index_url}")


def _node_bin_dir(data_home: Path) -> Path:
    """Return the directory that contains the node binary after extraction."""
    return data_home / "node"


def _extractall_no_traversal(
    tar: tarfile.TarFile, dest: Path
) -> None:  # pragma: no cover — Python < 3.11.4 fallback only
    """Extract *tar* into *dest*, rejecting any member that escapes *dest*.

    Replicates the ``filter="data"`` traversal/symlink guard for the rare
    Python < 3.11.4 patch releases where that argument is unavailable.
    """
    dest_resolved = dest.resolve()

    def _within(target: Path) -> bool:
        resolved = target.resolve()
        return resolved == dest_resolved or dest_resolved in resolved.parents

    for member in tar.getmembers():
        if not _within(dest_resolved / member.name):
            raise tarfile.TarError(f"unsafe path in tarball: {member.name!r}")
        if (member.issym() or member.islnk()) and not _within(
            (dest_resolved / member.name).parent / member.linkname
        ):
            raise tarfile.TarError(f"unsafe link in tarball: {member.name!r}")
    tar.extractall(path=dest)


def download_node(
    data_home: Path,
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> str:
    """Download portable Node.js tarball and extract to ``data_home/node/``.

    Returns the path to the node binary.
    """
    version = _latest_node_version()
    dist_name = _node_dist_name()
    mirror = _node_mirror()
    tarball_name = f"node-v{version}-{dist_name}.tar.gz"
    url = f"{mirror}/v{version}/{tarball_name}"

    node_dir = _node_bin_dir(data_home)
    bin_path = node_dir / "bin" / "node"
    if detect_node() == str(bin_path):
        return str(bin_path)

    node_dir.mkdir(parents=True, exist_ok=True)

    print(f"downloading Node.js v{version} ({dist_name}) …", file=stdout)
    stdout.flush()

    tmp_path: Path | None = None
    hasher = hashlib.sha256()
    try:
        with urlopen(Request(url, method="GET"), timeout=300) as resp:
            with tempfile.NamedTemporaryFile(
                suffix=".tar.gz", delete=False, dir=node_dir
            ) as tmp:
                tmp_path = Path(tmp.name)
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    tmp.write(chunk)
                    hasher.update(chunk)
    except (URLError, OSError) as exc:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"failed to download Node.js from {url}: {exc}") from exc

    # Verify the tarball against the published checksum *before* extracting, so a
    # tampered/corrupt download (e.g. a poisoned mirror) can never be unpacked
    # and run.
    expected_digest = _expected_node_sha256(
        mirror=mirror, version=version, tarball_name=tarball_name
    )
    actual_digest = hasher.hexdigest()
    if actual_digest != expected_digest:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"Node.js tarball checksum mismatch for {tarball_name}: "
            f"expected {expected_digest}, got {actual_digest}"
        )

    try:
        # Remove previous extraction if it exists
        for p in list(node_dir.glob("node-v*")):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        for p in list(node_dir.iterdir()):
            if p.name.startswith("node-v") and p.is_dir():
                shutil.rmtree(p, ignore_errors=True)

        with tarfile.open(tmp_path, "r:gz") as tar:
            # filter="data" rejects members with absolute paths, "..", or that
            # would escape node_dir (tar-slip), and is required on Python 3.14+
            # where the default-less extractall is an error.  The filter arg
            # landed in 3.11.4; fall back for older 3.11 patch releases.
            try:
                tar.extractall(path=node_dir, filter="data")
            except TypeError:  # pragma: no cover — Python < 3.11.4 only
                _extractall_no_traversal(tar, node_dir)
    except (tarfile.TarError, OSError) as exc:
        raise RuntimeError(f"failed to extract Node.js tarball: {exc}") from exc
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    # The extracted dir is ``node-v{version}-{dist}``.
    # Find it and symlink or note the bin path.
    extracted_dir: Path | None = None
    for entry in node_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(f"node-v{version}"):
            extracted_dir = entry
            break

    if extracted_dir is None:
        raise RuntimeError(f"Node.js tarball extracted but dir not found in {node_dir}")

    actual_bin = extracted_dir / "bin" / "node"
    if not actual_bin.exists():
        raise RuntimeError(f"node binary not found at {actual_bin}")

    # Verify it runs
    try:
        result = subprocess.run(
            [str(actual_bin), "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            raise RuntimeError(f"downloaded node exited with {result.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"downloaded node failed to start: {exc}") from exc

    return str(actual_bin)


def ensure_node(
    data_home: Path,
    *,
    interactive: bool = True,
    stdout: TextIO,
    stderr: TextIO,
) -> str | None:
    """Ensure Node.js >= {_MIN_NODE_MAJOR} is available.

    Checks system PATH first.  If not found and ``interactive=True``, prompts
    the user before downloading.  In non-interactive mode (e.g. ``pzi init
    --setup``) downloads automatically.

    Returns the path to the node binary, or ``None`` if the user declined.
    """
    node = detect_node()
    if node is not None:
        return node

    target = _node_bin_dir(data_home)

    if interactive:
        print(file=stderr)
        print(f"Node.js >= {_MIN_NODE_MAJOR} not found on PATH.", file=stderr)
        print(file=stderr)
        print("  [1] Install Node.js manually, then retry `pzi server`", file=stderr)
        print(f"  [2] Let pzi download portable Node.js to {target}/ (~40MB)", file=stderr)
        print(file=stderr)
        try:
            choice = input("Choose [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\ncancelled", file=stderr)
            return None
        if choice != "2":
            print(
                "Install Node.js >=22 manually, then run `pzi server`.",
                file=stderr,
            )
            return None

    try:
        path = download_node(data_home, stdout=stdout, stderr=stderr)
        print(f"Node.js installed to {path}", file=stdout)
        return path
    except RuntimeError as exc:
        print(f"failed to download Node.js: {exc}", file=stderr)
        if not interactive:
            print("install Node.js >=22 manually, then retry.", file=stderr)
        return None
