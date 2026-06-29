"""Translation-server bootstrap and process management.

Pure logic: detect available runtimes, download portable Node.js, clone and install
translation-server, manage subprocess lifecycle.  No container dependency.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NotRequired, TextIO, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pzi.node_runtime import ensure_node

# ---------------------------------------------------------------------------
# Pinned repository references.
#
# Each entry maps to a shallow clone target.  The ``ref`` field must be a
# commit hash, tag, or branch name.  Use ``main`` during development and
# freeze to a commit before each pzi release so that mid-release translator
# changes do not break existing installs.
# ---------------------------------------------------------------------------

_TS_REPOS: list[dict[str, str]] = [
    {
        "name": "translation-server",
        "url": "https://github.com/zotero/translation-server.git",
        "ref": "d88a8d5384456439962edfef129b14841b09af6d",
        "dest": ".",
    },
    {
        "name": "translators",
        "url": "https://github.com/zotero/translators.git",
        "ref": "854f85cd3418f03c52909dd717a3f780d68c14f5",
        "dest": "modules/translators",
    },
    {
        "name": "utilities",
        "url": "https://github.com/zotero/utilities.git",
        "ref": "1dd38e27edf81e9d9c4161c957b7efb7f5681ac3",
        "dest": "modules/utilities",
    },
    {
        "name": "translate",
        "url": "https://github.com/zotero/translate.git",
        "ref": "d08300c2c01a4d6ef325f05cbefc6c138a99f811",
        "dest": "modules/translate",
    },
    {
        "name": "zotero-schema",
        "url": "https://github.com/zotero/zotero-schema.git",
        "ref": "62e983a2e575fe9b9a3677ad7c9772080b67a1e4",
        "dest": "modules/zotero-schema",
    },
]

_TS_DEFAULT_PORT = 1969
_SENTINEL_FILENAME = ".pzi-installed"

# ---------------------------------------------------------------------------
# Translation-server bootstrap
# ---------------------------------------------------------------------------


def _sentinel_path(ts_dir: Path) -> Path:
    return ts_dir / _SENTINEL_FILENAME


def _write_sentinel(ts_dir: Path) -> None:
    """Write the sentinel file recording current pzi version and repo refs."""
    from pzi import cli_version_text

    lines = [f"pzi_version = {cli_version_text()}"]
    for repo in _TS_REPOS:
        lines.append(f"{repo['name']}_ref = {repo['ref']}")
    _sentinel_path(ts_dir).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_sentinel(ts_dir: Path) -> dict[str, str] | None:
    """Parse sentinel file into a dict, or None if missing."""
    sp = _sentinel_path(ts_dir)
    if not sp.exists():
        return None
    result: dict[str, str] = {}
    for line in sp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line:
            key, val = line.split("=", 1)
            result[key.strip()] = val.strip()
    return result


def _needs_reinstall(ts_dir: Path) -> bool:
    """Return True if translation-server dir is missing or out of date."""
    from pzi import cli_version_text

    sentinel = _read_sentinel(ts_dir)
    if sentinel is None:
        return True
    if sentinel.get("pzi_version") != cli_version_text():
        return True
    # Check that all repos have matching refs
    for repo in _TS_REPOS:
        key = f"{repo['name']}_ref"
        if sentinel.get(key) != repo["ref"]:
            return True
    return False


def _apply_cookie_patch(file_path: Path, patch_type: str) -> str | None:
    """Apply cookie-bridge patch to a translation-server JS source file.

    Uses flexible regex anchors that tolerate whitespace / formatting
    changes in upstream Zotero source.  Falls back to progressively
    broader searches when the primary anchor is not found.

    Returns a warning string if the patch did not apply (no anchor found),
    or ``None`` on success (patch applied or already present).
    """
    content = file_path.read_text(encoding="utf-8")

    # Already patched?
    if "pzi cookie bridge" in content:
        return None

    if patch_type == "session":
        return _patch_session(content, file_path)
    elif patch_type == "endpoint":
        return _patch_endpoint(content, file_path)
    else:
        raise ValueError(f"unknown patch type: {patch_type}")


def _patch_session(content: str, file_path: Path) -> str | None:
    """Inject cookie bridge after ``_cookieSandbox`` assignment in webSession.js."""
    m = _find_session_anchor(content)
    if m:
        patched = content[: m.end()] + "\n" + _SESSION_BLOCK + content[m.end() :]
        file_path.write_text(patched, encoding="utf-8")
        return None
    # Last resort: any mention of _cookieSandbox.
    if "this._cookieSandbox" not in content:
        return (
            f"cookie-bridge patch (session) did not apply to "
            f"{file_path.name} — upstream Zotero source may have changed; "
            f"browser cookies will not be forwarded to the translation server"
        )
    return None  # silently skip (symbol exists, no safe insertion point)


_SESSION_BLOCK = (
    "                                // --- pzi cookie bridge: inject browser cookies ---\n"
    "                                if (this._cookies) {\n"
    "                                        var _pziCookies = this._cookies.split(/;\\s*/);\n"
    "                                        for (var _i = 0; _i < _pziCookies.length; _i++) {\n"
    "                                                var _c = _pziCookies[_i].trim();\n"
    "                                                if (_c) {\n"
    "                                                        this._cookieSandbox.setCookie(_c, url);\n"  # noqa: E501
    "                                                }\n"
    "                                        }\n"
    "                                }\n"
    "                                // --- end pzi patch ---"
)


def _patch_endpoint(content: str, file_path: Path) -> str | None:
    """Inject cookie forwarding before ``handleURL()`` in webEndpoint.js."""
    m = _find_endpoint_anchor(content)
    if m:
        patched = content[: m.start()] + _ENDPOINT_BLOCK + "\n" + content[m.start() :]
        file_path.write_text(patched, encoding="utf-8")
        return None
    return (
        f"cookie-bridge patch (endpoint) did not apply to "
        f"{file_path.name} — upstream Zotero source may have changed; "
        f"browser cookies will not be forwarded to the translation server"
    )


_ENDPOINT_BLOCK = (
    "                        // --- pzi cookie bridge: forward cookies to session ---\n"
    "                        if (data && typeof data.cookies === \"string\" && data.cookies) {\n"
    "                                session._cookies = data.cookies;\n"
    "                        }\n"
    "                        // --- end pzi patch ---"
)


def _find_session_anchor(content: str) -> re.Match[str] | None:
    """Return the first match for ``_cookieSandbox`` assignment in webSession.js.

    Flexible: tolerates renamed jar function, extra whitespace.
    Falls back to the WebSession constructor body.
    """
    # Primary: "this._cookieSandbox = <expr>;"
    m = re.search(r"this\._cookieSandbox\s*=\s*[^;\n]+;", content)
    if m:
        return m
    # Fallback: WebSession constructor body
    m = re.search(r"function\s+WebSession\s*\([^)]*\)\s*\{", content)
    return m


def _find_endpoint_anchor(content: str) -> re.Match[str] | None:
    """Return the first match for ``handleURL()`` in webEndpoint.js.

    Flexible: matches ``await session.handleURL();`` or ``session.handleURL();``.
    Falls back to any ``handleURL()`` call.
    """
    m = re.search(r"(?:await\s+)?session\.handleURL\(\);", content)
    if m:
        return m
    m = re.search(r"handleURL\(\)", content)
    return m


def _build_cookie_patch(file_path: Path, patch_type: str) -> tuple[str, str] | None:
    """Generate a unified diff and patched content for a cookie-bridge patch.

    Returns ``(diff_text, patched_content)``, or ``None`` if the patch is
    already applied or the anchor line cannot be found (upstream changed).
    Does NOT modify any files.
    """
    content = file_path.read_text(encoding="utf-8")

    # Already patched?
    if "pzi cookie bridge" in content:
        return None

    # Check if any anchor exists (flexible — tolerates whitespace/renames).
    if patch_type == "session":
        anchor_present = (
            _find_session_anchor(content) is not None
            or "this._cookieSandbox" in content
        )
        code = _SESSION_BLOCK
        template = "session"
    elif patch_type == "endpoint":
        anchor_present = _find_endpoint_anchor(content) is not None
        code = _ENDPOINT_BLOCK
        template = "endpoint"
    else:
        raise ValueError(f"unknown patch type: {patch_type}")

    if not anchor_present:
        return None  # upstream changed

    # Build patched content for diff preview.
    # For session: insert block after first anchor match.
    if template == "session":
        m = _find_session_anchor(content)
        if m is None:
            m = re.search(r"(this\._cookieSandbox)", content)
        if m is None:
            return None
        patched = content[: m.end()] + "\n" + code + content[m.end() :]
    else:
        m = _find_endpoint_anchor(content)
        if m is None:
            return None
        patched = content[: m.start()] + code + "\n" + content[m.start() :]

    if patched == content:
        return None

    # Generate unified diff
    diff_lines = list(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{file_path.name}",
            tofile=f"b/{file_path.name}",
            lineterm="",
        )
    )
    return "".join(diff_lines), patched


def _patch_cookie_bridge(ts_dir: Path) -> bool:
    """Apply cookie-bridge patches via ``patch`` CLI subprocess.

    Returns ``True`` if all patches applied successfully (or were already
    present).  Returns ``False`` if any anchor line cannot be found (upstream
    Zotero source has changed).
    """
    patch_files = [
        (ts_dir / "src" / "webSession.js", "session"),
        (ts_dir / "src" / "webEndpoint.js", "endpoint"),
    ]
    ok = True
    for js_file, ptype in patch_files:
        if not js_file.exists():
            continue
        build_result = _build_cookie_patch(js_file, ptype)
        if build_result is None:
            if "pzi cookie bridge" not in js_file.read_text(encoding="utf-8"):
                ok = False  # anchor missing
            continue
        diff_text, patched_content = build_result

        # Write diff to temp file and apply via patch CLI
        diff_path = js_file.parent / f".{js_file.name}.pzi.patch"
        diff_path.write_text(diff_text, encoding="utf-8")
        try:
            subprocess.run(
                ["patch", "-p0", str(js_file), str(diff_path)],
                cwd=str(ts_dir),
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # patch CLI failed or not available — apply directly
            js_file.write_text(patched_content, encoding="utf-8")
        finally:
            diff_path.unlink(missing_ok=True)

    # Verification: check that all files contain the expected markers
    markers = {
        ts_dir / "src" / "webSession.js": "_pziCookies",
        ts_dir / "src" / "webEndpoint.js": "session._cookies",
    }
    for file_path, marker in markers.items():
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            if marker not in content and "pzi cookie bridge" not in content:
                ok = False
    return ok


def _clone_repo(
    url: str,
    ref: str,
    dest: Path,
    *,
    max_retries: int = 2,
    retry_delay: float = 3.0,
) -> None:
    """Shallow-clone a single repo at the given ref into dest, with retry.

    ``ref`` may be a branch name, tag, or commit hash.  For branches and
    tags we use ``--branch`` for a single-step clone.  For commit hashes
    we must clone the default branch first, then fetch + checkout the
    specific hash (``--branch`` does not accept commit hashes).
    """
    if dest.exists() and (dest / ".git").exists():
        return  # already cloned — user must ``pzi services update`` to refresh
    dest.parent.mkdir(parents=True, exist_ok=True)

    _ref_is_hash = bool(re.fullmatch(r"[0-9a-f]{40}", ref))

    last_exc: subprocess.CalledProcessError | None = None
    for attempt in range(max_retries + 1):
        try:
            if _ref_is_hash:
                # Clone default branch, then checkout the specific commit.
                subprocess.run(
                    ["git", "clone", "--depth=1", url, str(dest)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "-C", str(dest), "fetch", "--depth=1", "origin", ref],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "-C", str(dest), "checkout", "--detach", ref],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            else:
                subprocess.run(
                    ["git", "clone", "--depth=1", "--branch", ref, url, str(dest)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return
        except subprocess.CalledProcessError as exc:
            last_exc = exc
            if attempt < max_retries:
                import time as _time

                _time.sleep(retry_delay)
                # Clean up partial clone
                if dest.exists():
                    import shutil as _shutil

                    _shutil.rmtree(dest, ignore_errors=True)
    raise last_exc  # type: ignore[misc]


def ensure_translation_server(
    data_home: Path,
    node_bin: str,
    *,
    stdout: TextIO,
    stderr: TextIO,
) -> Path | None:
    """Clone/install translation-server into ``data_home/ts/``.

    Returns the path to the translation-server directory, or ``None`` on
    failure.
    """
    ts_dir = data_home / "ts"

    if not _needs_reinstall(ts_dir):
        return ts_dir

    if not shutil.which("git"):
        print(
            "git is not installed.  Install git and retry `pzi services up`.",
            file=stderr,
        )
        return None

    # Remove stale directory if sentinel mismatch
    if ts_dir.exists():
        print("removing outdated translation-server installation …", file=stdout)
        shutil.rmtree(ts_dir, ignore_errors=True)

    ts_dir.mkdir(parents=True, exist_ok=True)

    for repo in _TS_REPOS:
        dest = ts_dir / repo["dest"]
        print(f"cloning {repo['name']} …", file=stdout)
        stdout.flush()
        try:
            _clone_repo(repo["url"], repo["ref"], dest)
        except subprocess.CalledProcessError as exc:
            print(
                f"failed to clone {repo['name']}: {exc.stderr.strip()}",
                file=stderr,
            )
            return None

    # Apply cookie-bridge patches
    web_session = ts_dir / "src" / "webSession.js"
    web_endpoint = ts_dir / "src" / "webEndpoint.js"
    patch_warnings: list[str] = []
    if web_session.exists():
        warning = _apply_cookie_patch(web_session, "session")
        if warning:
            patch_warnings.append(warning)
    if web_endpoint.exists():
        warning = _apply_cookie_patch(web_endpoint, "endpoint")
        if warning:
            patch_warnings.append(warning)
    if patch_warnings:
        for w in patch_warnings:
            print(f"WARNING: {w}", file=stderr)

    # Run npm install
    print("installing translation-server dependencies (npm install) …", file=stdout)
    stdout.flush()
    npm_registry = os.environ.get("PZI_NPM_REGISTRY")
    npm_env = os.environ.copy()
    if npm_registry:
        npm_env["npm_config_registry"] = npm_registry

    try:
        subprocess.run(
            [node_bin, str(_npm_cli_path(node_bin)), "install", "--production"],
            cwd=str(ts_dir),
            env=npm_env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"npm install failed: {exc.stderr.strip()}", file=stderr)
        return None

    _write_sentinel(ts_dir)
    print(f"translation-server installed to {ts_dir}", file=stdout)
    return ts_dir


def _npm_cli_path(node_bin: str) -> Path:
    """Return the path to the npm CLI script bundled with the Node.js install."""
    node_path = Path(node_bin)
    # npm is at <node_dir>/lib/node_modules/npm/bin/npm-cli.js
    candidates = [
        node_path.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        node_path.parent / "npm",
        node_path.parent / "npm.cmd",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fallback: assume npm is on PATH
    npm = shutil.which("npm")
    if npm:
        return Path(npm)
    raise RuntimeError("npm not found alongside node binary")


# ---------------------------------------------------------------------------
# Process lifecycle
# ---------------------------------------------------------------------------


def start_ts(
    node_bin: str,
    ts_dir: Path,
    port: int = _TS_DEFAULT_PORT,
    stderr_log: Path | None = None,
) -> subprocess.Popen[bytes]:
    """Start translation-server as a bound child subprocess.

    Returns the ``Popen`` handle.  The child runs in its own session/process
    group (``start_new_session=True``) so the caller can tear down the whole
    group via :func:`terminate_ts` when the owning process exits.  No PID file
    is written and the child is never detached to run on its own.
    If ``stderr_log`` is given, stderr is redirected there; otherwise it goes
    to ``DEVNULL``.
    """
    stderr_handle: TextIO | None = None
    stderr_dest: int | TextIO = subprocess.DEVNULL
    if stderr_log is not None:
        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        stderr_handle = stderr_log.open("w")
        stderr_dest = stderr_handle

    proc = subprocess.Popen(
        [node_bin, str(ts_dir / "src" / "server.js")],
        cwd=str(ts_dir),
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=stderr_dest,
        start_new_session=True,
    )
    # The child has dup'd the fd; close our copy so it isn't leaked for the
    # server's lifetime.
    if stderr_handle is not None:
        stderr_handle.close()
    return proc


def terminate_ts(proc: subprocess.Popen[bytes], *, grace_seconds: float = 5.0) -> None:
    """Terminate a held translation-server child (and its process group).

    Sends SIGTERM to the child's process group, waits up to ``grace_seconds``,
    then SIGKILL if still alive.  Falls back to signalling the process directly
    on platforms without process groups.  Operates on the live ``Popen`` handle
    rather than a PID file, so there is no orphaned-process bookkeeping.
    """
    if proc.poll() is not None:
        return

    def _signal(sig: int) -> None:
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
                return
            except OSError:
                pass
        try:
            proc.send_signal(sig)
        except OSError:
            pass

    _signal(signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.2)

    _signal(signal.SIGKILL)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def is_ts_reachable(url: str, *, timeout: float = 2.0) -> bool:
    """Return True if translation-server responds at ``url``."""
    try:
        req = Request(url.rstrip("/"), method="GET")
        urlopen(req, timeout=timeout)
        return True
    except HTTPError:
        return True  # server responded (just not 2xx)
    except (URLError, OSError, ValueError):
        return False


def wait_for_ts(
    url: str,
    *,
    timeout: float = 90.0,
    stdout: TextIO,
    stderr: TextIO,
    proc: subprocess.Popen[bytes] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> bool:
    """Poll translation-server until reachable or timeout. Returns True if ready.

    If *proc* is provided, monitors the subprocess and fails fast if it exits
    before the server becomes reachable.  If *should_abort* is provided and
    returns ``True`` (e.g. the owning ``pzi server`` is shutting down), the wait
    returns ``False`` promptly instead of blocking out the full timeout.
    """
    health_url = url.rstrip("/")
    started_at = time.monotonic()
    deadline = started_at + timeout
    attempt = 0
    while time.monotonic() < deadline:
        # Abort promptly on shutdown rather than holding the caller for the
        # remaining timeout (the watchdog passes its stop event here).
        if should_abort is not None and should_abort():
            return False
        # Fail fast if the subprocess died
        if proc is not None and proc.poll() is not None:
            returncode = proc.returncode
            print(
                f"translation-server exited with code {returncode} "
                f"(PID {proc.pid}) — check logs at the data-home directory",
                file=stderr,
            )
            return False

        attempt += 1
        try:
            urlopen(Request(health_url, method="GET"), timeout=2)
            print(f"translation-server ready (attempt {attempt})", file=stdout)
            return True
        except HTTPError:
            print(f"translation-server ready (attempt {attempt})", file=stdout)
            return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(2)
    print(
        f"translation-server did not become ready within {timeout:.0f}s — "
        "check `pzi services status`, or run `pzi server` to start it",
        file=stderr,
    )
    return False


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------


def _ts_url_from_config(config: dict[str, object]) -> str | None:
    url = config.get("translation_server_url")
    if isinstance(url, str) and url.strip():
        return url
    return None


def _port_from_ts_url(ts_url: str) -> int:
    """Extract the port from a translation-server URL, or the default."""
    if ts_url and ":" in ts_url.split("//")[-1]:
        port_str = ts_url.rsplit(":", 1)[-1]
        try:
            return int(port_str)
        except ValueError:
            pass
    return _TS_DEFAULT_PORT


class BackendHandle(TypedDict):
    """Result of :func:`backend_session`.

    For an ``owned`` backend the restart inputs (``node_bin``, ``ts_dir``,
    ``port``, ``stderr_log``) are populated so a watchdog can re-launch a dead
    child; they are absent for reused/unmanaged backends.
    """

    url: str | None
    ready: bool
    owned: bool
    proc: subprocess.Popen[bytes] | None
    node_bin: NotRequired[str]
    ts_dir: NotRequired[Path]
    port: NotRequired[int]
    stderr_log: NotRequired[Path]


@contextmanager
def backend_session(
    config: dict[str, object],
    config_path: str,
    home_dir: str,
    *,
    interactive: bool = True,
    stdout: TextIO,
    stderr: TextIO,
) -> Iterator[BackendHandle]:
    """Provide a reachable translation-server for the duration of the block.

    Reuses an already-reachable server (``owned=False``) or, when none is
    running, bootstraps Node.js + translation-server and starts it as a bound
    child (``owned=True``).  An owned child is terminated when the block exits,
    so the backend never outlives the foreground process that started it — no
    PID files, no detached daemon.

    ``config_path`` is accepted for symmetry with the rest of the bootstrap
    helpers; the server is located purely from ``config``
    (``translation_server_url`` and ``pzi_data_home``).
    """
    ts_url = _ts_url_from_config(config)

    # No URL configured, or auto-start disabled: nothing for us to manage.
    if ts_url is None or os.environ.get("PZI_SKIP_AUTO_START"):
        yield {"url": ts_url, "ready": True, "owned": False, "proc": None}
        return

    if is_ts_reachable(ts_url):
        yield {"url": ts_url, "ready": True, "owned": False, "proc": None}
        return

    raw_home = config.get("pzi_data_home", home_dir)
    data_home = Path(str(raw_home)).expanduser()

    node = ensure_node(data_home, interactive=interactive, stdout=stdout, stderr=stderr)
    if node is None:
        yield {"url": ts_url, "ready": False, "owned": False, "proc": None}
        return

    ts_dir = ensure_translation_server(data_home, node, stdout=stdout, stderr=stderr)
    if ts_dir is None:
        yield {"url": ts_url, "ready": False, "owned": False, "proc": None}
        return

    port = _port_from_ts_url(ts_url)
    print(f"starting translation-server on port {port} …", file=stdout)
    stdout.flush()
    stderr_log = data_home / "ts-stderr.log"
    proc = start_ts(node, ts_dir, port=port, stderr_log=stderr_log)
    try:
        ready = wait_for_ts(ts_url, stdout=stdout, stderr=stderr, proc=proc)
        yield {
            "url": ts_url, "ready": ready, "owned": True, "proc": proc,
            "node_bin": node, "ts_dir": ts_dir, "port": port, "stderr_log": stderr_log,
        }
    finally:
        terminate_ts(proc)


# ---------------------------------------------------------------------------
# Watchdog: keep a long-lived `pzi server`'s owned TS child alive
# ---------------------------------------------------------------------------


class TranslationServerWatchdog:
    """Monitor an owned translation-server child and restart it if it dies.

    A long-running ``pzi server`` keeps the translation-server as a bound
    child. If that child crashes (OOM, SIGKILL) every capture fails until a
    human restarts it. This watchdog polls liveness on a background thread and,
    when the child is gone or unreachable, warns once and attempts a single
    restart. If the restart fails (or the replacement never becomes ready) it
    gives up to avoid a thrash loop; capture requests then surface the existing
    "not reachable" error.

    The polling primitives are injected so the observation logic is unit-tested
    via :meth:`tick` without real threads, timing, or subprocesses. The
    watchdog owns only the children *it* starts: :meth:`stop` terminates a
    restarted child, while the original child remains owned by
    :func:`backend_session`.
    """

    def __init__(
        self,
        *,
        ts_url: str,
        proc: subprocess.Popen[bytes],
        node_bin: str,
        ts_dir: Path,
        port: int,
        stderr_log: Path | None,
        stdout: TextIO,
        stderr: TextIO,
        interval: float = 30.0,
        auto_restart: bool = True,
        is_reachable: Callable[..., bool] = is_ts_reachable,
        start: Callable[..., subprocess.Popen[bytes]] = start_ts,
        wait: Callable[..., bool] = wait_for_ts,
        terminate: Callable[..., None] = terminate_ts,
    ) -> None:
        self._ts_url = ts_url
        self._initial_proc = proc
        self._proc = proc
        self._node_bin = node_bin
        self._ts_dir = ts_dir
        self._port = port
        self._stderr_log = stderr_log
        self._stdout = stdout
        self._stderr = stderr
        self._interval = interval
        self._auto_restart = auto_restart
        self._is_reachable = is_reachable
        self._start = start
        self._wait = wait
        self._terminate = terminate

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._warned = False
        self._gave_up = False

    @property
    def current_proc(self) -> subprocess.Popen[bytes]:
        """The child currently being monitored (may be a restarted one)."""
        with self._lock:
            return self._proc

    def start(self) -> None:
        """Spawn the background polling thread (daemon, never outlives stop)."""
        self._thread = threading.Thread(
            target=self._run, name="pzi-ts-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        """Stop polling and terminate any child the watchdog itself started."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None
        with self._lock:
            if self._proc is not self._initial_proc:
                self._terminate(self._proc)

    def _run(self) -> None:
        # `Event.wait` returns True once stop() is called, ending the loop; it
        # also provides the inter-tick delay without a busy spin.
        while not self._stop_event.wait(self._interval):
            self.tick()

    def tick(self) -> None:
        """Perform one liveness observation and, if needed, one restart."""
        with self._lock:
            if self._gave_up:
                return
            proc = self._proc
            alive = proc.poll() is None
            reachable = alive and self._is_reachable(self._ts_url, timeout=1.0)
            if alive and reachable:
                self._warned = False
                return

            if not self._warned:
                print(
                    "warning: translation-server became unreachable"
                    + (" — attempting restart" if self._auto_restart else ""),
                    file=self._stderr,
                )
                self._warned = True
            if not self._auto_restart:
                return

        # Restart OUTSIDE the lock: the readiness wait can block for tens of
        # seconds, and holding the lock here would make stop() (Ctrl-C on a
        # long-lived `pzi server`) block behind it.
        self._perform_restart(proc)

    def _perform_restart(self, dead_proc: subprocess.Popen[bytes]) -> None:
        """Restart the child once, without holding the lock across the wait.

        Disables further attempts on failure.  The blocking ``start``/``wait``
        run lock-free; only the short commit (swap proc / record give-up) takes
        the lock.  If :meth:`stop` races in while we wait, the freshly started
        replacement is torn down instead of adopted.
        """
        self._terminate(dead_proc)
        if self._stop_event.is_set():
            return
        try:
            new_proc = self._start(
                self._node_bin, self._ts_dir,
                port=self._port, stderr_log=self._stderr_log,
            )
        except Exception as exc:  # noqa: BLE001 — any start failure ends restarts
            print(
                f"warning: translation-server restart failed: {exc} — giving up",
                file=self._stderr,
            )
            with self._lock:
                self._gave_up = True
            return
        ready = self._wait(
            self._ts_url, stdout=self._stdout, stderr=self._stderr,
            proc=new_proc, should_abort=self._stop_event.is_set,
        )
        with self._lock:
            if self._stop_event.is_set():
                # Shutting down — don't adopt the replacement; tear it down so
                # it doesn't outlive the watchdog.
                self._terminate(new_proc)
                return
            if ready:
                self._proc = new_proc
                self._warned = False
                print("translation-server restarted", file=self._stdout)
            else:
                self._terminate(new_proc)
                print(
                    "warning: restarted translation-server did not become ready"
                    " — giving up",
                    file=self._stderr,
                )
                self._gave_up = True
