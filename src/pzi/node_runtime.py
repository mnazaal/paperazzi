"""Portable Node.js runtime bootstrap.

Pure logic: detect a system Node.js, or download and extract a portable build
into the pzi data home.  Independent of the Zotero translation-server — the only
coupling is that :func:`ensure_node` returns the node binary path that
``ts_backend`` then feeds to the translation-server install.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
import platform
import re
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


#: Caps for the two mirror reads. Both were `resp.read()` with no bound, so a
#: mirror (or anything that can answer as one) could stream until the process
#: ran out of memory. The real files are a few KB and a few MB respectively.
_MAX_CHECKSUMS_BYTES = 1 * 1024 * 1024
_MAX_INDEX_BYTES = 16 * 1024 * 1024


def _read_capped(resp: object, limit: int, url: str) -> bytes:
    """Read at most *limit* bytes, refusing anything longer."""
    data = resp.read(limit + 1)  # type: ignore[attr-defined]
    if len(data) > limit:
        raise RuntimeError(f"refusing oversized response from {url} (over {limit} bytes)")
    return data


def _expected_node_sha256(*, mirror: str, version: str, tarball_name: str) -> str:
    """Return the published sha256 for *tarball_name* from SHASUMS256.txt."""
    url = f"{mirror}/v{version}/SHASUMS256.txt"
    try:
        with urlopen(Request(url, method="GET"), timeout=30) as resp:
            text = _read_capped(resp, _MAX_CHECKSUMS_BYTES, url).decode("utf-8")
    except (URLError, OSError, http.client.IncompleteRead) as exc:
        # `IncompleteRead` is neither `URLError` nor `OSError`, so a truncated
        # response escaped both clauses and reached the caller as a traceback.
        raise RuntimeError(f"failed to fetch Node.js checksums from {url}: {exc}") from exc
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == tarball_name:
            return fields[0].lower()
    raise RuntimeError(f"no checksum for {tarball_name} in {url}")


def _node_version_ok(
    node_bin: str, min_version: tuple[int, int] = (_MIN_NODE_MAJOR, 0)
) -> bool:
    """Return True if *node_bin* runs and reports a version >= *min_version*."""
    try:
        result = subprocess.run(
            [node_bin, "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    version_str = result.stdout.strip().lstrip("v")
    try:
        parts = version_str.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return False
    return (major, minor) >= min_version


def detect_node(min_version: tuple[int, int] = (_MIN_NODE_MAJOR, 0)) -> str | None:
    """Return path to system Node.js binary if it meets min_version, else None."""
    node = shutil.which("node")
    if node is None:
        return None
    return node if _node_version_ok(node, min_version) else None


def _resolve_node_override(node_path: str | None) -> str | None:
    """Resolve an explicit Node.js binary from the ``PZI_NODE`` env var or the
    ``node_path`` config value (env wins).

    The value may be an absolute path or a bare command name on PATH. Returns
    the resolved path, or ``None`` when no override is set. Raises
    :class:`RuntimeError` when an override *is* set but does not resolve to a
    working Node.js >= {_MIN_NODE_MAJOR} — an explicit override that silently
    fell back to auto-detect/download would just hide the user's typo.
    """
    override = os.environ.get("PZI_NODE") or node_path
    if not override:
        return None
    resolved = shutil.which(override)
    if resolved is None and Path(override).is_file():
        resolved = override
    if resolved is not None and _node_version_ok(resolved):
        return resolved
    raise RuntimeError(
        f"PZI_NODE/node_path is set to {override!r} but it is not a working "
        f"Node.js >= {_MIN_NODE_MAJOR} (not found on PATH, not an executable "
        "file, or below the minimum version). Fix or unset it."
    )


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


def _pinned_node_version() -> str | None:
    """An explicitly pinned Node version, or None to take the newest major.

    Without a pin, `_latest_node_version()` returns whichever 22.x the mirror
    lists today, so the runtime pzi installs drifts with upstream and every
    point release triggers a fresh download. `PZI_NODE_VERSION` lets a user (or
    a reproducible build) fix it.
    """
    raw = os.environ.get("PZI_NODE_VERSION", "").strip().lstrip("v")
    if not raw:
        return None
    if not re.fullmatch(r"\d+\.\d+\.\d+", raw):
        raise RuntimeError(
            f"invalid PZI_NODE_VERSION {raw!r}: expected a full version like 22.11.0"
        )
    return raw


def _cached_node_binary(
    node_dir: Path, dist_name: str, *, version: str | None
) -> str | None:
    """A usable Node already in the data home, without asking the network.

    With *version* pinned only that one counts. Otherwise any extracted
    `node-vN-<dist>` of the required major will do — it is the same runtime the
    download would have produced, and preferring a fetch over it is what made
    an offline machine with a working Node report "could not install Node.js".
    """
    if version is not None:
        candidate = node_dir / f"node-v{version}-{dist_name}" / "bin" / "node"
        return str(candidate) if candidate.exists() and _node_binary_runs(candidate) else None
    try:
        entries = sorted(node_dir.glob(f"node-v*-{dist_name}"), reverse=True)
    except OSError:  # pragma: no cover — unreadable data home
        return None
    for entry in entries:
        candidate = entry / "bin" / "node"
        if candidate.exists() and _node_binary_runs(candidate):
            return str(candidate)
    return None


def _latest_node_version() -> str:
    """Return the latest v{_MIN_NODE_MAJOR}.x version string from the index."""
    mirror = _node_mirror()
    index_url = f"{mirror}/index.json"
    try:
        with urlopen(Request(index_url, method="GET"), timeout=15) as resp:
            import json

            data = json.loads(_read_capped(resp, _MAX_INDEX_BYTES, index_url))
    except (URLError, OSError, ValueError, http.client.IncompleteRead) as exc:
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


def download_node(
    data_home: Path,
    *,
    stdout: TextIO,
) -> str:
    """Download portable Node.js tarball and extract to ``data_home/node/``.

    Returns the path to the node binary.
    """
    dist_name = _node_dist_name()
    node_dir = _node_bin_dir(data_home)

    # Cached binary first, *before* any network call. `_latest_node_version()`
    # is a fetch and the cache key is the version string it returns, so a
    # working Node in the data home plus no network resolved to `None` — and
    # any upstream 22.x point release invalidated the cache and forced a fresh
    # download of a runtime that was already there and working.
    pinned = _pinned_node_version()
    cached = _cached_node_binary(node_dir, dist_name, version=pinned)
    if cached is not None:
        return cached

    version = pinned or _latest_node_version()
    mirror = _node_mirror()
    tarball_name = f"node-v{version}-{dist_name}.tar.gz"
    url = f"{mirror}/v{version}/{tarball_name}"

    existing_bin = node_dir / f"node-v{version}-{dist_name}" / "bin" / "node"
    if existing_bin.exists() and _node_binary_runs(existing_bin):
        return str(existing_bin)

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

    # Verify the tarball against the published checksum *before* extracting, so
    # a corrupt or truncated download can never be unpacked and run.
    #
    # This is an integrity check, not an authenticity one, and the comment here
    # used to claim it protected against "a poisoned mirror": `SHASUMS256.txt`
    # comes from the same origin as the tarball, so anything able to serve a
    # bad tarball can serve a matching digest. What actually bounds the risk is
    # that the mirror must be https (`_node_mirror` refuses otherwise, except
    # on loopback) and that `_MIN_NODE_MAJOR`/`PZI_NODE_VERSION` decide which
    # version is fetched rather than "whatever is newest today".
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
        with tarfile.open(tmp_path, "r:gz") as tar:
            # filter="data" rejects members with absolute paths, "..", or that
            # would escape node_dir (tar-slip), and is required on Python 3.14+
            # where the default-less extractall is an error.  The filter arg
            # landed in 3.11.4, which is the floor `requires-python` sets.
            tar.extractall(path=node_dir, filter="data")
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

    if not _node_binary_runs(actual_bin):
        raise RuntimeError(f"downloaded node failed to start: {actual_bin}")

    # Superseded extractions go only once the replacement is proven to run.
    # This used to happen before the tarball was even opened, so a truncated
    # download or a node binary that would not start left a machine that had a
    # working cached Node with none — on the command it ran to *get* one.
    for stale in list(node_dir.glob("node-v*")):
        if stale.is_dir() and stale != extracted_dir:
            shutil.rmtree(stale, ignore_errors=True)

    return str(actual_bin)


def _node_binary_runs(node_bin: Path) -> bool:
    """Return True if *node_bin* executes and exits cleanly."""
    try:
        result = subprocess.run(
            [str(node_bin), "--version"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def ensure_node(
    data_home: Path,
    *,
    interactive: bool = True,
    node_path: str | None = None,
    stdout: TextIO,
    stderr: TextIO,
) -> str | None:
    """Ensure Node.js >= {_MIN_NODE_MAJOR} is available.

    An explicit override (``PZI_NODE`` env var, else the ``node_path`` config
    value passed here) takes precedence over everything: if set it is used
    verbatim and pzi never prompts or downloads. A set-but-broken override is a
    hard error (returns ``None``) rather than a silent fallback, so a typo
    surfaces instead of triggering a surprise download.

    With no override, checks system PATH.  If Node.js is not found and
    ``interactive=True`` *and* a terminal is attached to stdin, prompts the
    user before downloading.  Otherwise (``interactive=False``, or
    ``interactive=True`` with no attached terminal — e.g. a systemd service)
    downloads automatically: there is nothing to prompt, and blocking on
    ``input()`` there would just raise ``EOFError`` immediately.

    Returns the path to the node binary, or ``None`` if the override was
    invalid, the user declined (interactive only), or the download failed.
    """
    try:
        override = _resolve_node_override(node_path)
    except RuntimeError as exc:
        print(str(exc), file=stderr)
        return None
    if override is not None:
        return override

    node = detect_node()
    if node is not None:
        return node

    target = _node_bin_dir(data_home)

    if interactive and sys.stdin.isatty():
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
    elif interactive:
        print(
            f"Node.js >= {_MIN_NODE_MAJOR} not found on PATH and no terminal is "
            f"attached — downloading portable Node.js to {target}/ automatically "
            "(~40MB). Put Node.js >=22 on PATH to skip this.",
            file=stderr,
        )

    try:
        path = download_node(data_home, stdout=stdout)
        print(f"Node.js installed to {path}", file=stdout)
        return path
    except RuntimeError as exc:
        print(f"failed to download Node.js: {exc}", file=stderr)
        if not interactive:
            print("install Node.js >=22 manually, then retry.", file=stderr)
        return None
