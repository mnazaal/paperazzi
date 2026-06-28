"""Tests for src/pzi/ts_backend.py."""

import io
import json
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from pzi import ts_backend

# ═══════════════════════════════════════════════════════════════════════════════
# detect_node
# ═══════════════════════════════════════════════════════════════════════════════

def test_detect_node_returns_path_when_version_ok() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.ts_backend.subprocess.run") as mock_run:
            mock_run.return_value = result(0, "v22.11.0\n", "")
            node = ts_backend.detect_node()
    assert node == "/usr/bin/node"


def test_detect_node_returns_none_when_not_found() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value=None):
        assert ts_backend.detect_node() is None


def test_detect_node_returns_none_when_version_too_old() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.ts_backend.subprocess.run") as mock_run:
            mock_run.return_value = result(0, "v18.0.0\n", "")
            node = ts_backend.detect_node()
    assert node is None


def test_detect_node_returns_none_on_nonzero_exit() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.ts_backend.subprocess.run") as mock_run:
            mock_run.return_value = result(1, "", "error")
            node = ts_backend.detect_node()
    assert node is None


def test_detect_node_returns_none_on_unparseable_version() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.ts_backend.subprocess.run") as mock_run:
            mock_run.return_value = result(0, "not-a-version\n", "")
            node = ts_backend.detect_node()
    assert node is None


def test_detect_node_handles_oserror() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.ts_backend.subprocess.run", side_effect=OSError):
            node = ts_backend.detect_node()
    assert node is None


def test_detect_node_handles_timeout() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/node"):
        with patch(
            "pzi.ts_backend.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["node"], 5),
        ):
            node = ts_backend.detect_node()
    assert node is None


# ═══════════════════════════════════════════════════════════════════════════════
# _node_dist_name
# ═══════════════════════════════════════════════════════════════════════════════

def test_node_dist_name_linux_x64() -> None:
    with patch.object(sys, "platform", "linux"):
        with patch("pzi.ts_backend.platform.machine", return_value="x86_64"):
            assert ts_backend._node_dist_name() == "linux-x64"


def test_node_dist_name_linux_arm64() -> None:
    with patch.object(sys, "platform", "linux"):
        with patch("pzi.ts_backend.platform.machine", return_value="aarch64"):
            assert ts_backend._node_dist_name() == "linux-arm64"


def test_node_dist_name_darwin_x64() -> None:
    with patch.object(sys, "platform", "darwin"):
        with patch("pzi.ts_backend.platform.machine", return_value="x86_64"):
            assert ts_backend._node_dist_name() == "darwin-x64"


def test_node_dist_name_darwin_arm64() -> None:
    with patch.object(sys, "platform", "darwin"):
        with patch("pzi.ts_backend.platform.machine", return_value="arm64"):
            assert ts_backend._node_dist_name() == "darwin-arm64"


def test_node_dist_name_unsupported_platform_raises() -> None:
    with patch.object(sys, "platform", "win32"):
        try:
            ts_backend._node_dist_name()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_node_dist_name_unsupported_arch_raises() -> None:
    with patch.object(sys, "platform", "linux"):
        with patch("pzi.ts_backend.platform.machine", return_value="mips"):
            try:
                ts_backend._node_dist_name()
                assert False, "expected RuntimeError"
            except RuntimeError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# _latest_node_version
# ═══════════════════════════════════════════════════════════════════════════════

def test_latest_node_version_returns_major_version() -> None:
    data = [
        {"version": "v23.1.0"},
        {"version": "v22.15.0"},
        {"version": "v22.14.0"},
    ]
    with patch(
        "pzi.ts_backend.urlopen",
        return_value=json_io(json.dumps(data)),
    ):
        ver = ts_backend._latest_node_version()
    assert ver == "22.15.0"


def test_latest_node_version_no_matching_major_raises() -> None:
    data = [{"version": "v20.0.0"}]
    with patch(
        "pzi.ts_backend.urlopen",
        return_value=json_io(json.dumps(data)),
    ):
        try:
            ts_backend._latest_node_version()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_latest_node_version_skips_entries_without_version() -> None:
    data = [
        {"version": None},
        {"version": "v22.10.0"},
    ]
    with patch(
        "pzi.ts_backend.urlopen",
        return_value=json_io(json.dumps(data)),
    ):
        ver = ts_backend._latest_node_version()
    assert ver == "22.10.0"


def test_latest_node_version_network_error_raises() -> None:
    with patch(
        "pzi.ts_backend.urlopen",
        return_value=json_io(json.dumps([])),
    ):
        try:
            ts_backend._latest_node_version()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# _node_bin_dir
# ═══════════════════════════════════════════════════════════════════════════════

def test_node_bin_dir() -> None:
    home = Path("/home/user/.local/share/pzi")
    assert ts_backend._node_bin_dir(home) == home / "node"


# ═══════════════════════════════════════════════════════════════════════════════
# ensure_node
# ═══════════════════════════════════════════════════════════════════════════════

def test_ensure_node_returns_system_node_if_found() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.detect_node", return_value="/usr/bin/node"):
        result = ts_backend.ensure_node(
            Path("/tmp"),
            interactive=True,
            stdout=stdout,
            stderr=stderr,
        )
    assert result == "/usr/bin/node"


def test_ensure_node_non_interactive_downloads() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.detect_node", return_value=None):
        with patch("pzi.ts_backend.download_node", return_value="/tmp/node/bin/node"):
            result = ts_backend.ensure_node(
                Path("/tmp"),
                interactive=False,
                stdout=stdout,
                stderr=stderr,
            )
    assert result == "/tmp/node/bin/node"


def test_ensure_node_download_failure_returns_none() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.detect_node", return_value=None):
        with patch(
            "pzi.ts_backend.download_node",
            side_effect=RuntimeError("download failed"),
        ):
            result = ts_backend.ensure_node(
                Path("/tmp"),
                interactive=False,
                stdout=stdout,
                stderr=stderr,
            )
    assert result is None
    assert "failed to download" in stderr.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# _needs_reinstall / sentinel helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_needs_reinstall_returns_true_when_no_sentinel(tmp_path: Path) -> None:
    assert ts_backend._needs_reinstall(tmp_path) is True


def test_needs_reinstall_returns_false_when_sentinel_matches(tmp_path: Path) -> None:
    ts_dir = tmp_path / "ts"
    ts_dir.mkdir()
    with patch(
        "pzi.cli_version_text",
        return_value="0.1.0",
    ):
        ts_backend._write_sentinel(ts_dir)
        assert ts_backend._needs_reinstall(ts_dir) is False


def test_needs_reinstall_returns_true_when_version_mismatch(tmp_path: Path) -> None:
    ts_dir = tmp_path / "ts"
    ts_dir.mkdir()
    with patch(
        "pzi.cli_version_text",
        return_value="0.1.0",
    ):
        ts_backend._write_sentinel(ts_dir)
    with patch(
        "pzi.cli_version_text",
        return_value="0.2.0",
    ):
        assert ts_backend._needs_reinstall(ts_dir) is True


def test_read_sentinel_missing() -> None:
    assert ts_backend._read_sentinel(Path("/nonexistent")) is None


def test_read_sentinel_parses_correctly(tmp_path: Path) -> None:
    sentinel = tmp_path / ".pzi-installed"
    sentinel.write_text("pzi_version = 0.1.0\ntranslation-server_ref = main\n")
    result = ts_backend._read_sentinel(tmp_path)
    assert result == {"pzi_version": "0.1.0", "translation-server_ref": "main"}


# ═══════════════════════════════════════════════════════════════════════════════
# _apply_cookie_patch
# ═══════════════════════════════════════════════════════════════════════════════

def test_apply_cookie_patch_session(tmp_path: Path) -> None:
    f = tmp_path / "webSession.js"
    f.write_text("this._cookieSandbox = cookieJar();")
    ts_backend._apply_cookie_patch(f, "session")
    content = f.read_text()
    assert "pzi cookie bridge" in content
    assert "this._cookieSandbox = cookieJar();" in content


def test_apply_cookie_patch_endpoint(tmp_path: Path) -> None:
    f = tmp_path / "webEndpoint.js"
    f.write_text("await session.handleURL();")
    ts_backend._apply_cookie_patch(f, "endpoint")
    content = f.read_text()
    assert "pzi cookie bridge" in content
    assert "await session.handleURL();" in content


def test_apply_cookie_patch_already_applied_noop(tmp_path: Path) -> None:
    f = tmp_path / "webSession.js"
    f.write_text(
        "// --- pzi cookie bridge ---\nthis._cookieSandbox = cookieJar();"
    )
    ts_backend._apply_cookie_patch(f, "session")
    assert "pzi cookie bridge" in f.read_text()


def test_apply_cookie_patch_unknown_type_raises(tmp_path: Path) -> None:
    f = tmp_path / "foo.js"
    f.write_text("// nothing")
    try:
        ts_backend._apply_cookie_patch(f, "bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# _build_cookie_patch  (hardened — diff/patch-based)
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_cookie_patch_session_generates_patch(tmp_path: Path) -> None:
    src = tmp_path / "webSession.js"
    src.write_text("this._cookieSandbox = cookieJar();")
    result = ts_backend._build_cookie_patch(src, "session")
    assert result is not None
    diff_text, patched = result
    assert "pzi cookie bridge" in diff_text
    assert "this._cookieSandbox = cookieJar()" in diff_text
    assert "pzi cookie bridge" in patched


def test_build_cookie_patch_endpoint_generates_patch(tmp_path: Path) -> None:
    src = tmp_path / "webEndpoint.js"
    src.write_text("await session.handleURL();")
    result = ts_backend._build_cookie_patch(src, "endpoint")
    assert result is not None
    diff_text, patched = result
    assert "pzi cookie bridge" in diff_text


def test_build_cookie_patch_already_applied_returns_none(tmp_path: Path) -> None:
    src = tmp_path / "webSession.js"
    src.write_text(
        "// --- pzi cookie bridge ---\nthis._cookieSandbox = cookieJar();"
    )
    result = ts_backend._build_cookie_patch(src, "session")
    assert result is None


def test_build_cookie_patch_anchor_not_found_returns_none(tmp_path: Path) -> None:
    src = tmp_path / "webSession.js"
    src.write_text("// completely different code\nlet x = 1;")
    result = ts_backend._build_cookie_patch(src, "session")
    assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# _patch_cookie_bridge  (hardened — uses patch CLI)
# ═══════════════════════════════════════════════════════════════════════════════

def test_patch_cookie_bridge_applies_and_verifies(tmp_path: Path) -> None:
    ts_dir = tmp_path / "ts"
    src_dir = ts_dir / "src"
    src_dir.mkdir(parents=True)
    session_js = src_dir / "webSession.js"
    session_js.write_text("this._cookieSandbox = cookieJar();")
    endpoint_js = src_dir / "webEndpoint.js"
    endpoint_js.write_text("await session.handleURL();")

    result = ts_backend._patch_cookie_bridge(ts_dir)
    assert result is True

    s = session_js.read_text()
    assert "pzi cookie bridge" in s
    assert "this._cookieSandbox = cookieJar()" in s
    e = endpoint_js.read_text()
    assert "pzi cookie bridge" in e


def test_patch_cookie_bridge_already_patched_is_ok(tmp_path: Path) -> None:
    ts_dir = tmp_path / "ts"
    src_dir = ts_dir / "src"
    src_dir.mkdir(parents=True)
    session_js = src_dir / "webSession.js"
    session_js.write_text(
        "// --- pzi cookie bridge ---\nthis._cookieSandbox = cookieJar();"
    )
    endpoint_js = src_dir / "webEndpoint.js"
    endpoint_js.write_text(
        "// --- pzi cookie bridge ---\nawait session.handleURL();"
    )

    result = ts_backend._patch_cookie_bridge(ts_dir)
    assert result is True


def test_patch_cookie_bridge_anchor_missing_fails(tmp_path: Path) -> None:
    ts_dir = tmp_path / "ts"
    src_dir = ts_dir / "src"
    src_dir.mkdir(parents=True)
    session_js = src_dir / "webSession.js"
    session_js.write_text("// completely different upstream code\nlet x = 1;")
    endpoint_js = src_dir / "webEndpoint.js"
    endpoint_js.write_text("// completely different upstream code\nlet y = 2;")

    result = ts_backend._patch_cookie_bridge(ts_dir)
    assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# cookie-bridge patch against realistic Zotero-shaped fixtures
#
# The trivial one-line tests above prove the regex anchors fire on a bare
# anchor string; these prove they still fire inside realistic constructor /
# handler bodies (indentation + surrounding code), so a drift in the flexible
# anchor regex relative to real upstream structure is caught.
# ═══════════════════════════════════════════════════════════════════════════════

_TS_JS_FIXTURES = Path(__file__).parent / "fixtures" / "ts_js"


def _copy_fixture(name: str, dest_dir: Path) -> Path:
    dest = dest_dir / name
    dest.write_text((_TS_JS_FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_apply_cookie_patch_session_realistic_fixture(tmp_path: Path) -> None:
    f = _copy_fixture("webSession.js", tmp_path)
    assert ts_backend._apply_cookie_patch(f, "session") is None
    content = f.read_text()
    assert "_pziCookies" in content
    assert "this._cookieSandbox = cookieSandbox" in content  # anchor preserved
    # Idempotent: a second pass is a no-op and does not duplicate the block.
    assert ts_backend._apply_cookie_patch(f, "session") is None
    assert f.read_text().count("pzi cookie bridge") == 1


def test_apply_cookie_patch_endpoint_realistic_fixture(tmp_path: Path) -> None:
    f = _copy_fixture("webEndpoint.js", tmp_path)
    assert ts_backend._apply_cookie_patch(f, "endpoint") is None
    content = f.read_text()
    assert "session._cookies" in content
    assert "await session.handleURL();" in content  # anchor preserved
    assert ts_backend._apply_cookie_patch(f, "endpoint") is None
    assert f.read_text().count("pzi cookie bridge") == 1


def test_patch_cookie_bridge_applies_to_realistic_fixtures(tmp_path: Path) -> None:
    # Exercises the real `patch -p0` CLI path end-to-end on realistic source:
    # _build_cookie_patch must produce a unified diff that re-applies cleanly.
    ts_dir = tmp_path / "ts"
    src_dir = ts_dir / "src"
    src_dir.mkdir(parents=True)
    _copy_fixture("webSession.js", src_dir)
    _copy_fixture("webEndpoint.js", src_dir)

    assert ts_backend._patch_cookie_bridge(ts_dir) is True
    assert "_pziCookies" in (src_dir / "webSession.js").read_text()
    assert "session._cookies" in (src_dir / "webEndpoint.js").read_text()


def test_patch_cookie_bridge_drifted_realistic_fixture_fails(tmp_path: Path) -> None:
    # Anchor removed (simulating an upstream rewrite): the patch must refuse
    # rather than corrupt the file — warn-don't-crash contract.
    ts_dir = tmp_path / "ts"
    src_dir = ts_dir / "src"
    src_dir.mkdir(parents=True)
    session = _copy_fixture("webSession.js", src_dir)
    drifted = session.read_text().replace("this._cookieSandbox", "this._jar")
    session.write_text(drifted)
    _copy_fixture("webEndpoint.js", src_dir)

    assert ts_backend._patch_cookie_bridge(ts_dir) is False
    # The drifted file is left without a half-applied patch block.
    assert "pzi cookie bridge" not in session.read_text()


# ═══════════════════════════════════════════════════════════════════════════════
# _npm_cli_path
# ═══════════════════════════════════════════════════════════════════════════════

def test_npm_cli_path_uses_bundled_npm(tmp_path: Path) -> None:
    lib_dir = tmp_path / "lib" / "node_modules" / "npm" / "bin"
    lib_dir.mkdir(parents=True)
    npm_cli = lib_dir / "npm-cli.js"
    npm_cli.write_text("// npm")
    node_bin = tmp_path / "bin" / "node"
    node_bin.parent.mkdir(parents=True)
    node_bin.write_text("")

    result = ts_backend._npm_cli_path(str(node_bin))
    assert result == npm_cli


def test_npm_cli_path_falls_back_to_path_npm() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value="/usr/bin/npm"):
        result = ts_backend._npm_cli_path("/nonexistent/bin/node")
    assert str(result) == "/usr/bin/npm"


def test_npm_cli_path_not_found_raises() -> None:
    with patch("pzi.ts_backend.shutil.which", return_value=None):
        try:
            ts_backend._npm_cli_path("/nonexistent/bin/node")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# is_ts_reachable
# ═══════════════════════════════════════════════════════════════════════════════

class FakeHTTPError(HTTPError):
    def __init__(self, code: int) -> None:
        super().__init__("http://x", code, "msg", {}, None)


def test_is_ts_reachable_200() -> None:
    with patch(
        "pzi.ts_backend.urlopen",
        return_value=io.BytesIO(b"ok"),
    ):
        assert ts_backend.is_ts_reachable("http://127.0.0.1:1969") is True


def test_is_ts_reachable_http_error_still_reachable() -> None:
    with patch(
        "pzi.ts_backend.urlopen",
        side_effect=FakeHTTPError(404),
    ):
        assert ts_backend.is_ts_reachable("http://127.0.0.1:1969") is True


def test_is_ts_reachable_connection_refused() -> None:
    with patch(
        "pzi.ts_backend.urlopen",
        side_effect=URLError("refused"),
    ):
        assert ts_backend.is_ts_reachable("http://127.0.0.1:1969") is False


# ═══════════════════════════════════════════════════════════════════════════════
# wait_for_ts
# ═══════════════════════════════════════════════════════════════════════════════

def test_wait_for_ts_success() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.urlopen", return_value=io.BytesIO(b"ok")):
        with patch("pzi.ts_backend.time.sleep"):
            assert (
                ts_backend.wait_for_ts(
                    "http://127.0.0.1:1969", timeout=10, stdout=stdout, stderr=stderr
                )
                is True
            )


def test_wait_for_ts_timeout() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.urlopen", side_effect=URLError("refused")):
        with patch("pzi.ts_backend.time.sleep"):
            assert (
                ts_backend.wait_for_ts(
                    "http://127.0.0.1:1969", timeout=0.1, stdout=stdout, stderr=stderr
                )
                is False
            )
    assert "did not become ready" in stderr.getvalue()


def test_wait_for_ts_fails_fast_when_process_dead() -> None:
    """When proc is provided and the process has exited, fail immediately."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    dead_proc = MagicMock()
    dead_proc.poll.return_value = 1  # process exited with code 1
    dead_proc.pid = 9999
    dead_proc.stderr = io.StringIO("Error: Cannot find module")

    with patch("pzi.ts_backend.time.sleep"):
        result = ts_backend.wait_for_ts(
            "http://127.0.0.1:1969",
            timeout=90,
            stdout=stdout,
            stderr=stderr,
            proc=dead_proc,
        )
    assert result is False
    assert "exited" in stderr.getvalue()


def test_wait_for_ts_polls_when_process_alive() -> None:
    """When proc is alive (poll returns None), wait normally without failing."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    live_proc = MagicMock()
    live_proc.poll.return_value = None  # still running

    # Server becomes reachable on second attempt
    urlopen_calls = [URLError("refused"), io.BytesIO(b"ok")]
    with patch("pzi.ts_backend.urlopen", side_effect=urlopen_calls):
        with patch("pzi.ts_backend.time.sleep"):
            result = ts_backend.wait_for_ts(
                "http://127.0.0.1:1969",
                timeout=10,
                stdout=stdout,
                stderr=stderr,
                proc=live_proc,
            )
    assert result is True
    assert "ready" in stdout.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# start_ts / terminate_ts
# ═══════════════════════════════════════════════════════════════════════════════

def test_start_ts_launches_subprocess(tmp_path: Path) -> None:
    with patch("pzi.ts_backend.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        proc = ts_backend.start_ts("/usr/bin/node", tmp_path)
    assert proc is mock_proc
    mock_popen.assert_called_once()
    args = mock_popen.call_args[0][0]
    assert args[0] == "/usr/bin/node"
    assert str(args[1]).endswith("src/server.js")


def test_start_ts_uses_new_session(tmp_path: Path) -> None:
    """The child runs in its own process group so it can be reaped as a group."""
    with patch("pzi.ts_backend.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        ts_backend.start_ts("/usr/bin/node", tmp_path)
    assert mock_popen.call_args[1]["start_new_session"] is True


def test_start_ts_stderr_log(tmp_path: Path) -> None:
    """When stderr_log is provided, Popen stderr should be the opened file."""
    stderr_log = tmp_path / "ts-stderr.log"
    with patch("pzi.ts_backend.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_popen.return_value = mock_proc
        ts_backend.start_ts("/usr/bin/node", tmp_path,
                            stderr_log=stderr_log)
    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args[1]
    assert "stderr" in kwargs
    assert kwargs["stderr"] is not subprocess.DEVNULL
    # The file should have been opened for writing
    assert stderr_log.exists()


def test_start_ts_stderr_devnull_when_no_log(tmp_path: Path) -> None:
    """When stderr_log is None, stderr goes to DEVNULL (silent fallback)."""
    with patch("pzi.ts_backend.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_popen.return_value = mock_proc
        ts_backend.start_ts("/usr/bin/node", tmp_path)
    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args[1]
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_terminate_ts_noop_when_already_exited() -> None:
    proc = MagicMock()
    proc.poll.return_value = 0  # already exited
    with patch("pzi.ts_backend.os.killpg") as mock_killpg:
        ts_backend.terminate_ts(proc)
    mock_killpg.assert_not_called()


def test_terminate_ts_sigterm_then_exits() -> None:
    proc = MagicMock()
    # alive at the guard poll, then exits after SIGTERM
    proc.poll.side_effect = [None, 0]
    with patch("pzi.ts_backend.os.killpg") as mock_killpg, \
            patch("pzi.ts_backend.os.getpgid", return_value=4242), \
            patch("pzi.ts_backend.time.sleep"):
        ts_backend.terminate_ts(proc)
    sigs = [c[0][1] for c in mock_killpg.call_args_list]
    assert signal.SIGTERM in sigs
    assert signal.SIGKILL not in sigs


def test_terminate_ts_force_kills_after_timeout() -> None:
    proc = MagicMock()
    proc.poll.return_value = None  # never exits on its own
    with patch("pzi.ts_backend.os.killpg") as mock_killpg, \
            patch("pzi.ts_backend.os.getpgid", return_value=4242), \
            patch("pzi.ts_backend.time.monotonic", side_effect=[0, 10]), \
            patch("pzi.ts_backend.time.sleep"):
        ts_backend.terminate_ts(proc)
    sigs = [c[0][1] for c in mock_killpg.call_args_list]
    assert signal.SIGTERM in sigs
    assert signal.SIGKILL in sigs


# ═══════════════════════════════════════════════════════════════════════════════
# _ts_url_from_config
# ═══════════════════════════════════════════════════════════════════════════════

def test_ts_url_from_config_valid() -> None:
    url = ts_backend._ts_url_from_config(
        {"translation_server_url": "http://127.0.0.1:1969"}
    )
    assert url == "http://127.0.0.1:1969"


def test_ts_url_from_config_missing() -> None:
    assert ts_backend._ts_url_from_config({}) is None


def test_ts_url_from_config_empty() -> None:
    assert ts_backend._ts_url_from_config({"translation_server_url": "  "}) is None


def test_ts_url_from_config_not_string() -> None:
    assert ts_backend._ts_url_from_config({"translation_server_url": 123}) is None


# ═══════════════════════════════════════════════════════════════════════════════
# backend_session
# ═══════════════════════════════════════════════════════════════════════════════

def test_backend_session_reuses_reachable_server() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.is_ts_reachable", return_value=True):
        with ts_backend.backend_session(
            {"translation_server_url": "http://127.0.0.1:1969"},
            "/tmp/config.toml", "/home/user",
            stdout=stdout, stderr=stderr,
        ) as backend:
            assert backend["ready"] is True
            assert backend["owned"] is False
            assert backend["proc"] is None


def test_backend_session_no_url_is_ready_and_unowned() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with ts_backend.backend_session(
        {}, "/tmp/config.toml", "/home/user", stdout=stdout, stderr=stderr,
    ) as backend:
        assert backend["ready"] is True
        assert backend["owned"] is False


def test_backend_session_skip_auto_start_does_not_probe(monkeypatch) -> None:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch(
        "pzi.ts_backend.is_ts_reachable",
        side_effect=AssertionError("should not probe when auto-start is skipped"),
    ):
        with ts_backend.backend_session(
            {"translation_server_url": "http://127.0.0.1:1969"},
            "/tmp/config.toml", "/home/user",
            stdout=stdout, stderr=stderr,
        ) as backend:
            assert backend["ready"] is True
            assert backend["owned"] is False


def test_backend_session_spawns_and_reaps_owned_child(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PZI_SKIP_AUTO_START", raising=False)
    stdout = io.StringIO()
    stderr = io.StringIO()
    fake_proc = MagicMock()
    with patch("pzi.ts_backend.is_ts_reachable", return_value=False), \
            patch("pzi.ts_backend.ensure_node", return_value="/usr/bin/node"), \
            patch("pzi.ts_backend.ensure_translation_server", return_value=tmp_path / "ts"), \
            patch("pzi.ts_backend.start_ts", return_value=fake_proc) as mock_start, \
            patch("pzi.ts_backend.wait_for_ts", return_value=True), \
            patch("pzi.ts_backend.terminate_ts") as mock_terminate:
        with ts_backend.backend_session(
            {"translation_server_url": "http://127.0.0.1:1969",
             "pzi_data_home": str(tmp_path)},
            "/tmp/config.toml", str(tmp_path),
            stdout=stdout, stderr=stderr,
        ) as backend:
            assert backend["owned"] is True
            assert backend["ready"] is True
            assert backend["proc"] is fake_proc
            mock_terminate.assert_not_called()  # still inside the block
        # child is reaped on block exit
        mock_terminate.assert_called_once_with(fake_proc)
    mock_start.assert_called_once()


def test_backend_session_node_bootstrap_failure_is_not_ready(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PZI_SKIP_AUTO_START", raising=False)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.ts_backend.is_ts_reachable", return_value=False), \
            patch("pzi.ts_backend.ensure_node", return_value=None):
        with ts_backend.backend_session(
            {"translation_server_url": "http://127.0.0.1:1969",
             "pzi_data_home": str(tmp_path)},
            "/tmp/config.toml", str(tmp_path),
            stdout=stdout, stderr=stderr,
        ) as backend:
            assert backend["ready"] is False
            assert backend["owned"] is False


def test_clone_repo_uses_branch_for_non_hash_ref(tmp_path: Path) -> None:
    """A branch name / tag uses single-step ``git clone --branch <ref>``."""
    dest = tmp_path / "dest"
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return MagicMock()

    with patch("pzi.ts_backend.subprocess.run", side_effect=fake_run):
        ts_backend._clone_repo("https://example.com/repo.git", "main", dest)

    assert len(calls) == 1
    assert calls[0][:5] == ["git", "clone", "--depth=1", "--branch", "main"]


def test_clone_repo_uses_fetch_checkout_for_hash_ref(tmp_path: Path) -> None:
    """A 40-char hex ref uses clone + fetch + checkout, not --branch."""
    dest = tmp_path / "dest"
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with patch("pzi.ts_backend.subprocess.run", side_effect=fake_run):
        ts_backend._clone_repo(
            "https://example.com/repo.git",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            dest,
        )

    assert len(calls) == 3, f"expected 3 calls, got {len(calls)}: {calls}"
    # 1: clone default branch
    assert calls[0][:3] == ["git", "clone", "--depth=1"]
    # 2: fetch the specific commit
    assert calls[1][:4] == ["git", "-C", str(dest), "fetch"]
    assert calls[1][6] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    # 3: checkout
    assert calls[2][:4] == ["git", "-C", str(dest), "checkout"]


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def result(returncode: int, stdout: str, stderr: str) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def json_io(data: str) -> io.BytesIO:
    return io.BytesIO(data.encode("utf-8"))


# ═══════════════════════════════════════════════════════════════════════════════
# TranslationServerWatchdog  (tick() observation logic, no real threads)
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeProc:
    """Minimal Popen stand-in whose liveness is controlled by `_alive`."""

    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def poll(self):
        return None if self._alive else 1


def _make_watchdog(**overrides):
    defaults = dict(
        ts_url="http://127.0.0.1:1969",
        proc=_FakeProc(alive=True),
        node_bin="/usr/bin/node",
        ts_dir=Path("/ts"),
        port=1969,
        stderr_log=None,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        is_reachable=lambda url, timeout=2.0: True,
        start=lambda *a, **k: _FakeProc(alive=True),
        wait=lambda *a, **k: True,
        terminate=lambda *a, **k: None,
    )
    defaults.update(overrides)
    return ts_backend.TranslationServerWatchdog(**defaults)


def test_watchdog_tick_noop_when_alive_and_reachable() -> None:
    started: list = []
    w = _make_watchdog(start=lambda *a, **k: started.append(1) or _FakeProc())
    w.tick()
    assert started == []  # no restart while healthy


def test_watchdog_tick_restarts_dead_child_and_swaps_proc() -> None:
    dead = _FakeProc(alive=False)
    fresh = _FakeProc(alive=True)
    starts: list = []
    stderr = io.StringIO()

    w = _make_watchdog(
        proc=dead, stderr=stderr,
        start=lambda *a, **k: (starts.append(1), fresh)[1],
        wait=lambda *a, **k: True,
    )
    w.tick()

    assert len(starts) == 1
    assert w.current_proc is fresh
    assert "unreachable" in stderr.getvalue()


def test_watchdog_tick_restart_only_attempted_once_then_gives_up() -> None:
    dead = _FakeProc(alive=False)
    starts: list = []

    # Restart launches a child that never becomes ready: give up, don't thrash.
    w = _make_watchdog(
        proc=dead,
        start=lambda *a, **k: (starts.append(1), _FakeProc(alive=False))[1],
        wait=lambda *a, **k: False,
    )
    w.tick()
    w.tick()  # second tick must be a no-op (gave up)

    assert len(starts) == 1


def test_watchdog_tick_detect_and_warn_without_restart() -> None:
    dead = _FakeProc(alive=False)
    starts: list = []
    stderr = io.StringIO()

    w = _make_watchdog(
        proc=dead, stderr=stderr, auto_restart=False,
        start=lambda *a, **k: starts.append(1) or _FakeProc(),
    )
    w.tick()

    assert starts == []  # warn only, never restart
    assert "unreachable" in stderr.getvalue()
    assert "attempting restart" not in stderr.getvalue()


def test_watchdog_stop_terminates_only_a_restarted_child() -> None:
    # The original child is owned by backend_session, so stop() must not
    # terminate it; only a watchdog-started replacement is the watchdog's to
    # tear down.
    original = _FakeProc(alive=True)
    terminated: list = []

    w = _make_watchdog(proc=original, terminate=lambda p, **k: terminated.append(p))
    w.stop()
    assert terminated == []  # never started a replacement → nothing to kill

    fresh = _FakeProc(alive=True)
    w2 = _make_watchdog(
        proc=_FakeProc(alive=False),
        start=lambda *a, **k: fresh,
        wait=lambda *a, **k: True,
        terminate=lambda p, **k: terminated.append(p),
    )
    w2.tick()           # restarts → current_proc is `fresh`
    terminated.clear()
    w2.stop()
    assert terminated == [fresh]  # stop terminates the restarted child
