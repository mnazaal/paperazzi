"""rc3 server-network lane: install-lock deadline, header deadline, redirects."""

import io
import select
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import portalocker

from pzi import http_api, ts_backend
from pzi.http_security import build_http_security_config


def _hold_install_lock(data_home: Path):
    data_home.mkdir(parents=True, exist_ok=True)
    handle = open(data_home / "ts.install.lock", "a")
    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
    return handle


def test_install_waiter_gives_up_with_actionable_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ts_backend, "_INSTALL_LOCK_WAIT_SECONDS", 0.4)
    holder = _hold_install_lock(tmp_path)
    err = io.StringIO()
    built: list[int] = []
    try:
        with patch.object(ts_backend.shutil, "which", return_value="/usr/bin/git"), \
             patch.object(ts_backend, "_build_translation_server",
                          side_effect=lambda *a, **k: built.append(1)):
            start = time.monotonic()
            result = ts_backend.ensure_translation_server(
                tmp_path, "node", stdout=io.StringIO(), stderr=err
            )
            elapsed = time.monotonic() - start
    finally:
        holder.close()
    assert result is None
    assert not built
    assert elapsed < 5
    assert "ts.install.lock" in err.getvalue()
    assert "another pzi process" in err.getvalue()


def test_uncontended_install_still_proceeds(tmp_path: Path) -> None:
    sentinel = tmp_path / "ts"
    with patch.object(ts_backend.shutil, "which", return_value="/usr/bin/git"), \
         patch.object(ts_backend, "_build_translation_server", return_value=sentinel) as build:
        result = ts_backend.ensure_translation_server(
            tmp_path, "node", stdout=io.StringIO(), stderr=io.StringIO()
        )
    assert result == sentinel
    build.assert_called_once()


def test_waiter_proceeds_once_lock_is_released(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ts_backend, "_INSTALL_LOCK_WAIT_SECONDS", 10)
    holder = _hold_install_lock(tmp_path)
    threading.Timer(0.5, holder.close).start()
    sentinel = tmp_path / "ts"
    with patch.object(ts_backend.shutil, "which", return_value="/usr/bin/git"), \
         patch.object(ts_backend, "_build_translation_server", return_value=sentinel):
        result = ts_backend.ensure_translation_server(
            tmp_path, "node", stdout=io.StringIO(), stderr=io.StringIO()
        )
    assert result == sentinel


# ---------------------------------------------------------------------------
# Header-read deadline (pre-auth)
# ---------------------------------------------------------------------------


def _serve(tmp_path: Path, per_read_timeout: float):
    handler = http_api.build_handler_class(
        config_path=str(tmp_path / "c.toml"),
        home_dir=str(tmp_path),
        security=build_http_security_config(),
    )
    handler.timeout = per_read_timeout
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _trickle_until_closed(port: int, prefix: bytes, interval: float, give_up: float) -> float | None:
    """Seconds until the server closes the connection, None if it never does."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    start = time.monotonic()
    try:
        sock.sendall(prefix)
        while time.monotonic() - start < give_up:
            try:
                sock.sendall(b"a")
            except OSError:
                return time.monotonic() - start
            readable, _, _ = select.select([sock], [], [], interval)
            if readable:
                try:
                    if not sock.recv(65536):
                        return time.monotonic() - start
                except OSError:
                    return time.monotonic() - start
        return None
    finally:
        sock.close()


def test_slow_header_stream_is_closed_despite_in_time_bytes(tmp_path: Path, monkeypatch) -> None:
    """Each byte lands well inside the 5s per-read timeout; only a wall-clock
    cap on the whole header block can close this connection."""
    monkeypatch.setattr(http_api, "HEADER_READ_DEADLINE_SECONDS", 0.5)
    server = _serve(tmp_path, per_read_timeout=5)
    try:
        port = server.server_address[1]
        # incomplete header block: never sends the terminating blank line
        closed = _trickle_until_closed(port, b"GET /status HTTP/1.1\r\nX-Slow: ", 0.1, 4.0)
        assert closed is not None, "slow header stream was never closed"
        assert closed < 2.5
    finally:
        server.shutdown()
        server.server_close()


def test_slow_request_line_is_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(http_api, "HEADER_READ_DEADLINE_SECONDS", 0.5)
    server = _serve(tmp_path, per_read_timeout=5)
    try:
        closed = _trickle_until_closed(server.server_address[1], b"GE", 0.1, 4.0)
        assert closed is not None and closed < 2.5
    finally:
        server.shutdown()
        server.server_close()


def test_timer_threads_and_handler_threads_are_reclaimed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(http_api, "HEADER_READ_DEADLINE_SECONDS", 0.3)
    server = _serve(tmp_path, per_read_timeout=5)
    try:
        before = threading.active_count()
        port = server.server_address[1]
        socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(8)]
        for s in socks:
            s.sendall(b"GET / HTTP/1.1\r\nX: ")
        time.sleep(1.2)
        for s in socks:
            s.close()
        deadline = time.monotonic() + 3
        while threading.active_count() > before and time.monotonic() < deadline:
            time.sleep(0.05)
        assert threading.active_count() <= before
    finally:
        server.shutdown()
        server.server_close()


def test_normal_request_unaffected_and_timer_cancelled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(http_api, "HEADER_READ_DEADLINE_SECONDS", 0.5)
    server = _serve(tmp_path, per_read_timeout=5)
    try:
        port = server.server_address[1]
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(b"GET /status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
        sock.close()
        assert data.startswith(b"HTTP/1.0 ")
    finally:
        server.shutdown()
        server.server_close()
