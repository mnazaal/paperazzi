"""Tests for src/pzi/node_runtime.py."""

import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pzi import node_runtime

# ═══════════════════════════════════════════════════════════════════════════════
# detect_node
# ═══════════════════════════════════════════════════════════════════════════════

def test_detect_node_returns_path_when_version_ok() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.node_runtime.subprocess.run") as mock_run:
            mock_run.return_value = result(0, "v22.11.0\n", "")
            node = node_runtime.detect_node()
    assert node == "/usr/bin/node"


def test_detect_node_returns_none_when_not_found() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value=None):
        assert node_runtime.detect_node() is None


def test_detect_node_returns_none_when_version_too_old() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.node_runtime.subprocess.run") as mock_run:
            mock_run.return_value = result(0, "v18.0.0\n", "")
            node = node_runtime.detect_node()
    assert node is None


def test_detect_node_returns_none_on_nonzero_exit() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.node_runtime.subprocess.run") as mock_run:
            mock_run.return_value = result(1, "", "error")
            node = node_runtime.detect_node()
    assert node is None


def test_detect_node_returns_none_on_unparseable_version() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.node_runtime.subprocess.run") as mock_run:
            mock_run.return_value = result(0, "not-a-version\n", "")
            node = node_runtime.detect_node()
    assert node is None


def test_detect_node_handles_oserror() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value="/usr/bin/node"):
        with patch("pzi.node_runtime.subprocess.run", side_effect=OSError):
            node = node_runtime.detect_node()
    assert node is None


def test_detect_node_handles_timeout() -> None:
    with patch("pzi.node_runtime.shutil.which", return_value="/usr/bin/node"):
        with patch(
            "pzi.node_runtime.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["node"], 5),
        ):
            node = node_runtime.detect_node()
    assert node is None


# ═══════════════════════════════════════════════════════════════════════════════
# _node_dist_name
# ═══════════════════════════════════════════════════════════════════════════════

def test_node_dist_name_linux_x64() -> None:
    with patch.object(sys, "platform", "linux"):
        with patch("pzi.node_runtime.platform.machine", return_value="x86_64"):
            assert node_runtime._node_dist_name() == "linux-x64"


def test_node_dist_name_linux_arm64() -> None:
    with patch.object(sys, "platform", "linux"):
        with patch("pzi.node_runtime.platform.machine", return_value="aarch64"):
            assert node_runtime._node_dist_name() == "linux-arm64"


def test_node_dist_name_darwin_x64() -> None:
    with patch.object(sys, "platform", "darwin"):
        with patch("pzi.node_runtime.platform.machine", return_value="x86_64"):
            assert node_runtime._node_dist_name() == "darwin-x64"


def test_node_dist_name_darwin_arm64() -> None:
    with patch.object(sys, "platform", "darwin"):
        with patch("pzi.node_runtime.platform.machine", return_value="arm64"):
            assert node_runtime._node_dist_name() == "darwin-arm64"


def test_node_dist_name_unsupported_platform_raises() -> None:
    with patch.object(sys, "platform", "win32"):
        try:
            node_runtime._node_dist_name()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_node_dist_name_unsupported_arch_raises() -> None:
    with patch.object(sys, "platform", "linux"):
        with patch("pzi.node_runtime.platform.machine", return_value="mips"):
            try:
                node_runtime._node_dist_name()
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
        "pzi.node_runtime.urlopen",
        return_value=json_io(json.dumps(data)),
    ):
        ver = node_runtime._latest_node_version()
    assert ver == "22.15.0"


def test_latest_node_version_no_matching_major_raises() -> None:
    data = [{"version": "v20.0.0"}]
    with patch(
        "pzi.node_runtime.urlopen",
        return_value=json_io(json.dumps(data)),
    ):
        try:
            node_runtime._latest_node_version()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_latest_node_version_skips_entries_without_version() -> None:
    data = [
        {"version": None},
        {"version": "v22.10.0"},
    ]
    with patch(
        "pzi.node_runtime.urlopen",
        return_value=json_io(json.dumps(data)),
    ):
        ver = node_runtime._latest_node_version()
    assert ver == "22.10.0"


def test_latest_node_version_network_error_raises() -> None:
    with patch(
        "pzi.node_runtime.urlopen",
        return_value=json_io(json.dumps([])),
    ):
        try:
            node_runtime._latest_node_version()
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# _node_bin_dir
# ═══════════════════════════════════════════════════════════════════════════════

def test_node_bin_dir() -> None:
    home = Path("/home/user/.local/share/pzi")
    assert node_runtime._node_bin_dir(home) == home / "node"


# ═══════════════════════════════════════════════════════════════════════════════
# ensure_node
# ═══════════════════════════════════════════════════════════════════════════════

def test_ensure_node_returns_system_node_if_found() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.node_runtime.detect_node", return_value="/usr/bin/node"):
        result = node_runtime.ensure_node(
            Path("/tmp"),
            interactive=True,
            stdout=stdout,
            stderr=stderr,
        )
    assert result == "/usr/bin/node"


def test_ensure_node_non_interactive_downloads() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.node_runtime.detect_node", return_value=None):
        with patch("pzi.node_runtime.download_node", return_value="/tmp/node/bin/node"):
            result = node_runtime.ensure_node(
                Path("/tmp"),
                interactive=False,
                stdout=stdout,
                stderr=stderr,
            )
    assert result == "/tmp/node/bin/node"


def test_ensure_node_download_failure_returns_none() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.node_runtime.detect_node", return_value=None):
        with patch(
            "pzi.node_runtime.download_node",
            side_effect=RuntimeError("download failed"),
        ):
            result = node_runtime.ensure_node(
                Path("/tmp"),
                interactive=False,
                stdout=stdout,
                stderr=stderr,
            )
    assert result is None
    assert "failed to download" in stderr.getvalue()


def test_ensure_node_no_tty_downloads_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """interactive=True but stdin not a TTY (systemd) must auto-download, not
    block on input() and silently cancel."""
    monkeypatch.delenv("PZI_NODE", raising=False)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.node_runtime.detect_node", return_value=None):
        with patch("pzi.node_runtime.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with patch(
                "pzi.node_runtime.download_node", return_value="/tmp/node/bin/node"
            ) as mock_dl:
                with patch("pzi.node_runtime.input") as mock_input:
                    result = node_runtime.ensure_node(
                        Path("/tmp"),
                        interactive=True,
                        stdout=stdout,
                        stderr=stderr,
                    )
    assert result == "/tmp/node/bin/node"
    mock_input.assert_not_called()
    mock_dl.assert_called_once()


def test_ensure_node_override_arg_used_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PZI_NODE", raising=False)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.node_runtime.shutil.which", return_value="/opt/node/bin/node"):
        with patch("pzi.node_runtime._node_version_ok", return_value=True):
            with patch("pzi.node_runtime.detect_node") as mock_detect:
                with patch("pzi.node_runtime.download_node") as mock_dl:
                    result = node_runtime.ensure_node(
                        Path("/tmp"),
                        interactive=True,
                        node_path="/opt/node/bin/node",
                        stdout=stdout,
                        stderr=stderr,
                    )
    assert result == "/opt/node/bin/node"
    mock_detect.assert_not_called()
    mock_dl.assert_not_called()


def test_ensure_node_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PZI_NODE", "/env/node")
    stdout = io.StringIO()
    stderr = io.StringIO()

    def _which(name: str) -> str | None:
        return name if name == "/env/node" else None

    with patch("pzi.node_runtime.shutil.which", side_effect=_which):
        with patch("pzi.node_runtime._node_version_ok", return_value=True):
            result = node_runtime.ensure_node(
                Path("/tmp"),
                interactive=True,
                node_path="/config/node",
                stdout=stdout,
                stderr=stderr,
            )
    assert result == "/env/node"


def test_ensure_node_broken_override_is_hard_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A set-but-invalid override must return None, not fall back to download."""
    monkeypatch.setenv("PZI_NODE", "/does/not/exist")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch("pzi.node_runtime.shutil.which", return_value=None):
        with patch("pzi.node_runtime.download_node") as mock_dl:
            result = node_runtime.ensure_node(
                Path("/tmp"),
                interactive=False,
                stdout=stdout,
                stderr=stderr,
            )
    assert result is None
    mock_dl.assert_not_called()
    assert "PZI_NODE" in stderr.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# _node_mirror  (PZI_NODE_MIRROR scheme validation)
# ═══════════════════════════════════════════════════════════════════════════════

def test_node_mirror_defaults_to_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PZI_NODE_MIRROR", raising=False)
    assert node_runtime._node_mirror() == "https://nodejs.org/dist"


def test_node_mirror_allows_custom_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PZI_NODE_MIRROR", "https://mirror.example.com/dist")
    assert node_runtime._node_mirror() == "https://mirror.example.com/dist"


def test_node_mirror_allows_http_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PZI_NODE_MIRROR", "http://127.0.0.1:8080/dist")
    assert node_runtime._node_mirror() == "http://127.0.0.1:8080/dist"


def test_node_mirror_rejects_http_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PZI_NODE_MIRROR", "http://evil.example.com/dist")
    with pytest.raises(RuntimeError, match="insecure PZI_NODE_MIRROR"):
        node_runtime._node_mirror()


# ═══════════════════════════════════════════════════════════════════════════════
# _expected_node_sha256
# ═══════════════════════════════════════════════════════════════════════════════

def test_expected_node_sha256_parses_matching_line() -> None:
    shasums = (
        "abc123  node-v22.15.0-linux-arm64.tar.gz\n"
        "deadbeef  node-v22.15.0-linux-x64.tar.gz\n"
    )
    with patch("pzi.node_runtime.urlopen", return_value=json_io(shasums)):
        digest = node_runtime._expected_node_sha256(
            mirror="https://nodejs.org/dist",
            version="22.15.0",
            tarball_name="node-v22.15.0-linux-x64.tar.gz",
        )
    assert digest == "deadbeef"


def test_expected_node_sha256_missing_tarball_raises() -> None:
    shasums = "abc123  node-v22.15.0-linux-arm64.tar.gz\n"
    with patch("pzi.node_runtime.urlopen", return_value=json_io(shasums)):
        with pytest.raises(RuntimeError, match="no checksum"):
            node_runtime._expected_node_sha256(
                mirror="https://nodejs.org/dist",
                version="22.15.0",
                tarball_name="node-v22.15.0-linux-x64.tar.gz",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# download_node  (checksum verification)
# ═══════════════════════════════════════════════════════════════════════════════

def test_download_node_rejects_checksum_mismatch(tmp_path: Path) -> None:
    tarball = b"not-a-real-tarball"
    shasums = f"{'0' * 64}  node-v22.15.0-linux-x64.tar.gz\n"
    with patch("pzi.node_runtime._latest_node_version", return_value="22.15.0"), \
         patch("pzi.node_runtime._node_dist_name", return_value="linux-x64"), \
         patch("pzi.node_runtime.detect_node", return_value=None), \
         patch(
             "pzi.node_runtime.urlopen",
             side_effect=[io.BytesIO(tarball), json_io(shasums)],
         ):
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            node_runtime.download_node(tmp_path, stdout=io.StringIO(), stderr=io.StringIO())
    # Tampered download must never be left on disk for extraction.
    node_dir = node_runtime._node_bin_dir(tmp_path)
    assert not any(node_dir.glob("*.tar.gz"))
    assert not any(node_dir.glob("node-v*"))


def test_download_node_passes_checksum_then_fails_extract(tmp_path: Path) -> None:
    tarball = b"not-a-real-tarball"
    good = hashlib.sha256(tarball).hexdigest()
    shasums = f"{good}  node-v22.15.0-linux-x64.tar.gz\n"
    with patch("pzi.node_runtime._latest_node_version", return_value="22.15.0"), \
         patch("pzi.node_runtime._node_dist_name", return_value="linux-x64"), \
         patch("pzi.node_runtime.detect_node", return_value=None), \
         patch(
             "pzi.node_runtime.urlopen",
             side_effect=[io.BytesIO(tarball), json_io(shasums)],
         ):
        # Checksum matches, so it proceeds past verification and only then trips
        # on the bogus (non-gzip) tarball during extraction.
        with pytest.raises(RuntimeError, match="extract"):
            node_runtime.download_node(tmp_path, stdout=io.StringIO(), stderr=io.StringIO())


def test_download_node_reuses_cached_binary(tmp_path: Path) -> None:
    """A previously extracted, runnable Node is reused without re-downloading."""
    node_dir = node_runtime._node_bin_dir(tmp_path)
    cached = node_dir / "node-v22.15.0-linux-x64" / "bin" / "node"
    cached.parent.mkdir(parents=True)
    cached.write_text("#!/bin/sh\n")
    with patch("pzi.node_runtime._latest_node_version", return_value="22.15.0"), \
         patch("pzi.node_runtime._node_dist_name", return_value="linux-x64"), \
         patch("pzi.node_runtime._node_binary_runs", return_value=True), \
         patch("pzi.node_runtime.urlopen") as mock_urlopen:
        result_path = node_runtime.download_node(
            tmp_path, stdout=io.StringIO(), stderr=io.StringIO()
        )
    assert result_path == str(cached)
    mock_urlopen.assert_not_called()


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
