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


# ---------------------------------------------------------------------------
# Pinning proxy
# ---------------------------------------------------------------------------

from pzi import pinning_proxy  # noqa: E402
from tests.test_rc3_net_fix import PUBLIC_IP, _answers, _proxy_for, _RawUpstream  # noqa: E402

KEEPALIVE_REPLY = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"


def _get(host: str = "a.test", path: str = "/") -> bytes:
    return f"GET http://{host}{path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()


def _read_until_end(sock: socket.socket) -> tuple[bytes, BaseException | None]:
    """Everything received, and the error that ended it (None for a clean FIN)."""
    data = b""
    try:
        while chunk := sock.recv(65536):
            data += chunk
    except OSError as exc:
        return data, exc
    return data, None


def test_next_request_on_a_connection_the_origin_kept_open_sees_it_end_quickly() -> None:
    up = _RawUpstream(KEEPALIVE_REPLY)  # ignores `Connection: close`, keeps the socket
    proxy = _proxy_for(up)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(_get())
            assert s.recv(65536).endswith(b"ok")
            started = time.monotonic()
            s.sendall(_get("b.test", "/second"))
            data, _ = _read_until_end(s)  # must end now, not after the 60 s idle limit
            elapsed = time.monotonic() - started
    finally:
        proxy.close()
        up.close()
    assert data == b""
    assert elapsed < 1.5  # the stand-in origin gives up itself after 3 s
    assert b"b.test" not in b"".join(up.received)


def test_client_half_close_after_its_request_still_gets_the_whole_reply() -> None:
    up = _RawUpstream(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok", honour_close=True)
    proxy = _proxy_for(up)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(_get())
            s.shutdown(socket.SHUT_WR)
            data, error = _read_until_end(s)
    finally:
        proxy.close()
        up.close()
    assert data.endswith(b"ok") and error is None


class _ResettingUpstream:
    """Sends a close-delimited head and part of a body, then resets or stalls."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import struct

        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.4 part")
            if self.mode == "reset":
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                conn.close()
            elif self.mode == "close":
                conn.close()
            # "stall": hold the socket open and say nothing more.

    def close(self) -> None:
        self.sock.close()


def _proxy_to(server: _ResettingUpstream) -> PinningProxy:
    def connect(address, timeout=None):
        return socket.create_connection(("127.0.0.1", server.port), timeout=timeout)

    return PinningProxy(getaddrinfo=_answers(PUBLIC_IP), connect=connect)


from pzi.pinning_proxy import PinningProxy  # noqa: E402


@pytest.mark.parametrize("mode", ["reset", "stall"])
def test_failed_close_delimited_plain_http_body_reaches_the_client_as_a_reset(
    monkeypatch, mode: str
) -> None:
    monkeypatch.setattr(pinning_proxy, "_PROXY_IDLE_SECONDS", 1.0)
    server = _ResettingUpstream(mode)
    proxy = _proxy_to(server)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(_get())
            data, error = _read_until_end(s)
    finally:
        proxy.close()
        server.close()
    assert b"%PDF-1.4 part" in data
    assert isinstance(error, ConnectionResetError)


def test_normal_close_of_a_close_delimited_plain_http_body_stays_a_clean_end(monkeypatch) -> None:
    server = _ResettingUpstream("close")
    proxy = _proxy_to(server)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(_get())
            data, error = _read_until_end(s)
    finally:
        proxy.close()
        server.close()
    assert data.endswith(b"%PDF-1.4 part")
    assert error is None


@pytest.mark.parametrize("dead", [2, 3, 4, 6])
def test_dead_addresses_never_use_up_the_budget_before_the_last_one(monkeypatch, dead: int) -> None:
    now = [0.0]
    monkeypatch.setattr(safe_http.time, "monotonic", lambda: now[0])
    live = object()
    calls: list[str] = []

    def connect(address, timeout=None):
        calls.append(address[0])
        if address[0] == "live":
            return live
        now[0] += timeout  # a black-holed address waits out its whole share
        raise TimeoutError

    class _Sock:
        def settimeout(self, value):
            pass

    live = _Sock()
    ips = [f"dead{i}" for i in range(dead)] + ["live"]
    got = safe_http.connect_first_reachable(ips, 80, timeout=15.0, connect=connect)
    assert got is live
    assert calls == ips


class _KeepAliveOrigin:
    """Answers each request with a short page and keeps the socket open, ignoring ``close``."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.requests = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        body = b"<html><body>ok</body></html>"
        try:
            while conn.recv(65536):
                self.requests += 1
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.sock.close()


@pytest.mark.browser
def test_chromium_next_fetch_to_a_keepalive_origin_does_not_stall() -> None:
    import pzi.browser_session as bs
    from tests.browser_probe import require_browser

    require_browser()
    origin = _KeepAliveOrigin()

    def connect(address, timeout=None):
        return socket.create_connection(("127.0.0.1", origin.port), timeout=timeout)

    script = """async () => {
        const out = [];
        for (const path of ['/a', '/b', '/c']) {
            const started = performance.now();
            const response = await fetch(path);
            await response.text();
            out.push([response.status, performance.now() - started]);
        }
        return out;
    }"""
    try:
        with bs.open_browser_session(
            "chromium", url_allowed=lambda u: True,
            getaddrinfo=_answers(PUBLIC_IP), connect=connect,
        ) as session:
            session.navigate("http://pub.test/page")
            results = session.page.evaluate(script)
    finally:
        origin.close()
    assert [status for status, _ in results] == [200, 200, 200]
    assert max(ms for _, ms in results) < 10_000
