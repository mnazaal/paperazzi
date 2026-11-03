"""rc3 server-network lane: install-lock deadline, header deadline, redirects."""

import io
import select
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import portalocker
import pytest

from pzi import http_api, pinning_proxy, ts_backend, ts_egress_guard
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


# ---------------------------------------------------------------------------
# Translation-server redirect / meta-refresh egress (real pinned backend)
# ---------------------------------------------------------------------------
_PUBLIC_IP = "93.184.216.34"  # a public address; the fake dial maps it to a local listener
_TS_DIR = Path.home() / ".local/share/pzi/ts"


def _installed_backend() -> tuple[str, Path] | None:
    """Node + translation-server checkout, only if the checkout is the pinned commit."""
    node = next(iter((Path.home() / ".local/share/pzi/node").glob("node-*/bin/node")), None)
    sentinel = _TS_DIR / ".pzi-installed"
    if node is None or not sentinel.is_file():
        return None
    pinned = next(r["ref"] for r in ts_backend._TS_REPOS if "translation-server" in r["url"])
    if f"translation-server_ref = {pinned}" not in sentinel.read_text():
        return None
    return str(node), _TS_DIR


# Resolved at import: an autouse fixture re-points HOME for each test.
_BACKEND = _installed_backend()


class _Recorder(BaseHTTPRequestHandler):
    hits: list[str]

    def log_message(self, *a) -> None:
        pass


def _listener(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.skipif(_BACKEND is None, reason="pinned translation-server not installed")
def test_pinned_backend_does_not_follow_redirect_or_meta_refresh_to_private(tmp_path: Path) -> None:
    assert _BACKEND is not None
    node, ts_dir = _BACKEND
    private_hits: list[str] = []

    class Private(_Recorder):
        def do_GET(self) -> None:
            private_hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><title>secret</title></html>")

    public_hits: list[str] = []
    private = _listener(Private)
    pport = private.server_address[1]

    class Public(_Recorder):
        def do_GET(self) -> None:
            public_hits.append(self.path)
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{pport}/via-redirect")
                self.end_headers()
                return
            body = (
                '<html><head><title>pub</title><meta http-equiv="refresh" '
                f'content="0;url=http://127.0.0.1:{pport}/via-meta"></head></html>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    public = _listener(Public)
    aport = public.server_address[1]

    def fake_getaddrinfo(host, port, *a, **k):
        if host == "pub.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, port))]
        return socket.getaddrinfo(host, port, *a, **k)

    def fake_connect(address, timeout=None):
        ip, port = address
        return socket.create_connection(("127.0.0.1", aport if ip == _PUBLIC_IP else port), timeout=5)

    ts_egress_guard.reset_shared_guard(
        pinning_proxy.PinningProxy(getaddrinfo=fake_getaddrinfo, connect=fake_connect)
    )
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    tport = s.getsockname()[1]
    s.close()
    proc = ts_backend.start_ts(node, ts_dir, port=tport, stderr_log=tmp_path / "ts.err")
    try:
        for _ in range(150):
            try:
                socket.create_connection(("127.0.0.1", tport), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        statuses = {}
        for path in ("/redir", "/meta"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{tport}/web",
                data=f"http://pub.test:{aport}{path}".encode(),
                headers={"Content-Type": "text/plain"},
            )
            try:
                statuses[path] = urllib.request.urlopen(req, timeout=30).status
            except urllib.error.HTTPError as exc:
                statuses[path] = exc.code
        # Sanity: the public hop itself works through the guard (meta page is
        # reached; only the refresh target is refused), so a zero count is not
        # just "the backend never fetched anything".
        assert statuses["/meta"] in (200, 300, 400, 500, 501), statuses
        # The public hop was fetched (through the guard) for both pages, so an
        # empty private list means the second hop was refused, not never tried.
        assert sorted(public_hits) == ["/meta", "/redir"], public_hits
        assert private_hits == [], private_hits
    finally:
        ts_backend.terminate_ts(proc)
        ts_egress_guard.reset_shared_guard(None)
        private.shutdown()
        public.shutdown()


def test_guard_refuses_private_and_dials_validated_ip_only() -> None:
    dialed: list[tuple[str, int]] = []

    def connect(address, timeout=None):
        dialed.append(address)
        raise OSError("no network in test")

    guard = pinning_proxy.PinningProxy(connect=connect)
    try:
        host, port = guard.url[len("http://"):].split(":")

        def ask(first_line: str) -> bytes:
            c = socket.create_connection((host, int(port)), timeout=5)
            c.sendall(f"{first_line}\r\nHost: x\r\n\r\n".encode())
            data = b""
            while chunk := c.recv(4096):
                data += chunk
            c.close()
            return data

        for line in (
            "GET http://127.0.0.1:9/x HTTP/1.1",
            "GET http://169.254.169.254/latest HTTP/1.1",
            "GET http://[::1]:9/x HTTP/1.1",
            "CONNECT 10.0.0.5:443 HTTP/1.1",
            "CONNECT localhost:443 HTTP/1.1",
        ):
            assert ask(line).startswith(b"HTTP/1.1 403 "), line
        assert dialed == []
    finally:
        guard.close()


def test_guard_tunnels_connect_to_a_public_address() -> None:
    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen(1)

    def serve() -> None:
        conn, _ = echo.accept()
        conn.sendall(conn.recv(100).upper())
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    guard = pinning_proxy.PinningProxy(
        getaddrinfo=lambda host, port, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, port))],
        connect=lambda address, timeout=None: socket.create_connection(echo.getsockname(), timeout=5),
    )
    try:
        host, port = guard.url[len("http://"):].split(":")
        c = socket.create_connection((host, int(port)), timeout=5)
        c.sendall(b"CONNECT pub.test:443 HTTP/1.1\r\nHost: pub.test:443\r\n\r\n")
        assert c.recv(4096).startswith(b"HTTP/1.1 200 ")
        c.sendall(b"hello")
        assert c.recv(100) == b"HELLO"
        c.close()
    finally:
        guard.close()
        echo.close()


@pytest.mark.skipif(_BACKEND is None, reason="pinned translation-server not installed")
@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not available")
def test_pinned_backend_reaches_https_through_the_guard_and_stays_public(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real captures are HTTPS: the CONNECT tunnel has to work with the real client.

    A guard that refused or broke CONNECT would stop every capture while the
    HTTP-only tests above stayed green. The certificate is self-signed, so the
    child is told to accept it; that setting is scoped to this test's child.
    """
    assert _BACKEND is not None
    node, ts_dir = _BACKEND
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=pub.test", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    private_hits: list[str] = []

    class Private(_Recorder):
        def do_GET(self) -> None:
            private_hits.append(self.path)
            self.send_response(200)
            self.end_headers()

    private = _listener(Private)
    pport = private.server_address[1]
    public_hits: list[str] = []

    class Public(_Recorder):
        def do_GET(self) -> None:
            public_hits.append(self.path)
            if self.path == "/to-private":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{pport}/via-https-redirect")
                self.end_headers()
                return
            body = b"<html><head><title>tls page</title></head><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    public = _listener(Public)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    public.socket = context.wrap_socket(public.socket, server_side=True)
    aport = public.server_address[1]

    def fake_getaddrinfo(host, port, *a, **k):
        if host == "pub.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, port))]
        return socket.getaddrinfo(host, port, *a, **k)

    def fake_connect(address, timeout=None):
        ip, port = address
        return socket.create_connection(("127.0.0.1", aport if ip == _PUBLIC_IP else port), timeout=5)

    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    ts_egress_guard.reset_shared_guard(
        pinning_proxy.PinningProxy(getaddrinfo=fake_getaddrinfo, connect=fake_connect)
    )
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    tport = s.getsockname()[1]
    s.close()
    proc = ts_backend.start_ts(node, ts_dir, port=tport, stderr_log=tmp_path / "ts.err")
    try:
        for _ in range(150):
            try:
                socket.create_connection(("127.0.0.1", tport), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        for path in ("/page", "/to-private"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{tport}/web",
                data=f"https://pub.test:{aport}{path}".encode(),
                headers={"Content-Type": "text/plain"},
            )
            try:
                urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError:
                pass  # no translator matches; the hit lists below are the evidence
        assert sorted(public_hits) == ["/page", "/to-private"], public_hits
        assert private_hits == [], private_hits
    finally:
        ts_backend.terminate_ts(proc)
        ts_egress_guard.reset_shared_guard(None)
        private.shutdown()
        public.shutdown()
