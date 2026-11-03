"""Regression tests for the rc3 final network re-review."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from pzi import safe_http

LOCAL_IPS = ["127.0.0.1", "127.0.0.2"]


class _SlowServer:
    """Accepts, waits ``delay`` seconds, then answers 200 with a short body."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.sock = socket.socket()
        self.sock.bind(("0.0.0.0", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn: socket.socket) -> None:
        with conn:
            conn.recv(65536)
            time.sleep(self.delay)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def slow_server():
    server = _SlowServer(delay=1.2)
    yield server
    server.close()


class _PassThroughContext:
    """Stands in for an SSLContext: the TLS layer keeps the socket's timeout anyway."""

    def wrap_socket(self, sock, server_hostname=None):
        return sock


def _patch_addresses(monkeypatch, ips):
    monkeypatch.setattr(safe_http, "resolve_pinned_ips", lambda *a, **k: list(ips))
    # Shorter than the server delay, so a leaked dial timeout is observable.
    monkeypatch.setattr(safe_http, "_PER_ADDRESS_CONNECT_SECONDS", 0.4)


@pytest.mark.parametrize("ips", [LOCAL_IPS[:1], LOCAL_IPS], ids=["one-address", "two-addresses"])
@pytest.mark.parametrize("https", [False, True], ids=["http-class", "https-class"])
def test_caller_read_timeout_is_restored_after_the_dial(monkeypatch, slow_server, ips, https):
    _patch_addresses(monkeypatch, ips)
    if https:
        conn = safe_http._PinnedHTTPSConnection("example.test", slow_server.port, timeout=30)
        conn._ssl_ctx = _PassThroughContext()  # type: ignore[assignment]
    else:
        conn = safe_http._PinnedHTTPConnection("example.test", slow_server.port, timeout=30)
    conn.connect()
    try:
        assert conn.sock.gettimeout() == 30
    finally:
        conn.close()


@pytest.mark.parametrize("ips", [LOCAL_IPS[:1], LOCAL_IPS], ids=["one-address", "two-addresses"])
def test_slow_first_byte_is_not_cut_by_the_per_address_dial_timeout(monkeypatch, slow_server, ips):
    _patch_addresses(monkeypatch, ips)
    conn = safe_http._PinnedHTTPConnection("example.test", slow_server.port, timeout=30)
    try:
        conn.request("GET", "/")
        assert conn.getresponse().read() == b"ok"
    finally:
        conn.close()


def test_default_timeout_sentinel_leaves_the_socket_default(monkeypatch, slow_server):
    _patch_addresses(monkeypatch, LOCAL_IPS)
    conn = safe_http._PinnedHTTPConnection("example.test", slow_server.port)
    conn.connect()
    try:
        assert conn.sock.gettimeout() == socket.getdefaulttimeout()
    finally:
        conn.close()
