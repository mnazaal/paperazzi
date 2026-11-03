"""Regression tests for the rc3 network-boundary review fixes."""

from __future__ import annotations

import pytest

from pzi.url_safety import public_ip_address


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "::127.0.0.1",  # IPv4-compatible
        "::7f00:1",
        "::ffff:127.0.0.1",  # IPv4-mapped
        "fec0::1",  # site-local
        "2002:7f00:1::",  # 6to4 of loopback
        "2002:0a00:0001::",  # 6to4 of 10.0.0.1
        "2001:0:4136:e378:8000:63bf:80ff:fffe",  # Teredo, client 127.0.0.1
        "2001:0:7f00:1::",  # Teredo with a loopback server
    ],
)
def test_ipv6_forms_carrying_private_ipv4_are_not_public(address: str) -> None:
    assert public_ip_address(address) is False


@pytest.mark.parametrize(
    "address",
    ["2606:4700:4700::1111", "2001:4860:4860::8888", "64:ff9b::808:808", "::ffff:8.8.8.8"],
)
def test_real_public_addresses_stay_public(address: str) -> None:
    assert public_ip_address(address) is True


# ---------------------------------------------------------------------------
# Pinning proxy: refusal body, address fallback, one request, cut bodies
# ---------------------------------------------------------------------------

import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from pzi.pinning_proxy import PinningProxy  # noqa: E402

PUBLIC_IP = "93.184.216.34"
PUBLIC_IP_2 = "93.184.216.35"


def _answers(*ips: str):
    def getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)) for ip in ips]

    return getaddrinfo


class _RawUpstream:
    """Accepts connections, records everything received, replies as told."""

    def __init__(self, reply: bytes = b"", *, honour_close: bool = False, stream: bool = False):
        self.received: list[bytes] = []
        self.reply = reply
        self.honour_close = honour_close
        self.stream = stream
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.sent = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        buf = b""
        try:
            conn.settimeout(3)
            buf = conn.recv(65536)
            self.received.append(buf)
            if self.honour_close:
                # Wait for the request to settle, answer, close: a polite server.
                conn.settimeout(0.5)
                try:
                    while more := conn.recv(65536):
                        self.received.append(more)
                except OSError:
                    pass
                conn.sendall(self.reply)
                return
            conn.sendall(self.reply)
            if self.stream:
                block = b"x" * 65536
                while True:
                    conn.sendall(block)
                    self.sent += len(block)
            # Ignore `Connection: close`: keep reading, as a hostile server would.
            while True:
                more = conn.recv(65536)
                if not more:
                    return
                self.received.append(more)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.sock.close()


def _proxy_for(upstream: _RawUpstream, *, answers=(PUBLIC_IP,), **kwargs) -> PinningProxy:
    def connect(address, timeout=None):
        return socket.create_connection(("127.0.0.1", upstream.port), timeout=timeout)

    return PinningProxy(getaddrinfo=_answers(*answers), connect=connect, **kwargs)


def _read_all(sock: socket.socket) -> bytes:
    data = b""
    while chunk := sock.recv(65536):
        data += chunk
    return data


def test_refusal_has_a_text_plain_body_naming_the_guard() -> None:
    proxy = PinningProxy(getaddrinfo=_answers("127.0.0.1"))
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"GET http://rebind.test/x HTTP/1.1\r\nHost: rebind.test\r\n\r\n")
            reply = _read_all(s)
    finally:
        proxy.close()
    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 403")
    assert b"content-type: text/plain" in head.lower()
    assert b"egress guard" in body and b"non-public" in body


def test_dead_first_address_falls_back_to_the_next_validated_one() -> None:
    up = _RawUpstream(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok", honour_close=True)
    dialed: list[str] = []

    def connect(address, timeout=None):
        dialed.append(address[0])
        if address[0] == PUBLIC_IP:
            raise ConnectionRefusedError
        return socket.create_connection(("127.0.0.1", up.port), timeout=timeout)

    proxy = PinningProxy(getaddrinfo=_answers(PUBLIC_IP, PUBLIC_IP_2), connect=connect)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"GET http://pub.test/ HTTP/1.1\r\nHost: pub.test\r\n\r\n")
            reply = _read_all(s)
    finally:
        proxy.close()
        up.close()
    assert dialed == [PUBLIC_IP, PUBLIC_IP_2]
    assert reply.startswith(b"HTTP/1.1 200")


def test_every_answer_must_still_be_public_before_any_dial() -> None:
    dialed: list[object] = []

    def connect(address, timeout=None):
        dialed.append(address)
        raise ConnectionRefusedError

    proxy = PinningProxy(getaddrinfo=_answers(PUBLIC_IP, "10.0.0.5"), connect=connect)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"GET http://pub.test/ HTTP/1.1\r\nHost: pub.test\r\n\r\n")
            reply = _read_all(s)
    finally:
        proxy.close()
    assert reply.startswith(b"HTTP/1.1 403") and dialed == []


def test_all_addresses_dead_is_a_502_with_a_reason() -> None:
    def connect(address, timeout=None):
        raise ConnectionRefusedError

    proxy = PinningProxy(getaddrinfo=_answers(PUBLIC_IP, PUBLIC_IP_2), connect=connect)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"GET http://pub.test/ HTTP/1.1\r\nHost: pub.test\r\n\r\n")
            reply = _read_all(s)
    finally:
        proxy.close()
    assert reply.startswith(b"HTTP/1.1 502") and b"text/plain" in reply.lower()


def test_second_request_on_a_plain_http_connection_is_not_forwarded() -> None:
    # The upstream ignores `Connection: close` and keeps reading.
    up = _RawUpstream(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    proxy = _proxy_for(up)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            first = b"GET http://attacker.test/a HTTP/1.1\r\nHost: attacker.test\r\n\r\n"
            second = (
                b"GET http://victim.example.org/account HTTP/1.1\r\n"
                b"Host: victim.example.org\r\nCookie: session=VICTIM\r\n\r\n"
            )
            s.sendall(first + second)  # pipelined in one segment
            time.sleep(0.3)
            s.sendall(second)  # and again after the first response
            time.sleep(0.5)
    finally:
        proxy.close()
        up.close()
    seen = b"".join(up.received)
    assert b"/a HTTP/1.1" in seen
    assert b"victim.example.org" not in seen and b"VICTIM" not in seen


def test_declared_request_body_is_forwarded_in_full_across_segments() -> None:
    up = _RawUpstream(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok", honour_close=True)
    proxy = _proxy_for(up)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(
                b"POST http://pub.test/up HTTP/1.1\r\nHost: pub.test\r\n"
                b"Content-Length: 10\r\n\r\nabcd"
            )
            time.sleep(0.2)
            s.sendall(b"efghij" + b"EXTRA-NOT-BODY")
            reply = _read_all(s)
    finally:
        proxy.close()
        up.close()
    seen = b"".join(up.received)
    assert reply.startswith(b"HTTP/1.1 200")
    assert seen.endswith(b"abcdefghij")
    assert b"EXTRA" not in seen


def test_chunked_request_body_is_refused_explicitly() -> None:
    up = _RawUpstream(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    proxy = _proxy_for(up)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(
                b"POST http://pub.test/up HTTP/1.1\r\nHost: pub.test\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n"
            )
            reply = _read_all(s)
    finally:
        proxy.close()
        up.close()
    assert reply.startswith(b"HTTP/1.1 501") and up.received == []


def _read_until_failure(sock: socket.socket) -> tuple[int, bool]:
    """(bytes read, ended by an error rather than a clean close)."""
    total = 0
    try:
        while chunk := sock.recv(65536):
            total += len(chunk)
    except OSError:
        return total, True
    return total, False


def test_close_delimited_body_over_the_cap_ends_in_a_reset_not_a_clean_close() -> None:
    up = _RawUpstream(b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\n\r\n", stream=True)
    proxy = _proxy_for(up, max_response_bytes=256 * 1024)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"GET http://pub.test/big.pdf HTTP/1.1\r\nHost: pub.test\r\n\r\n")
            total, errored = _read_until_failure(s)
    finally:
        proxy.close()
        up.close()
    assert errored, "a cut body must not look like a complete close-delimited body"
    assert total <= 256 * 1024 + 1024


def test_tunnel_over_the_cap_ends_in_a_reset_not_a_clean_close() -> None:
    up = _RawUpstream(b"", stream=True)
    proxy = _proxy_for(up, max_response_bytes=256 * 1024)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"CONNECT pub.test:443 HTTP/1.1\r\nHost: pub.test:443\r\n\r\n")
            s.recv(4096)  # 200 Connection Established
            s.sendall(b"hello")  # wakes the stand-in upstream, which then streams
            total, errored = _read_until_failure(s)
    finally:
        proxy.close()
        up.close()
    assert errored


# ---------------------------------------------------------------------------
# Real Chromium: a cut close-delimited body is a failure, not a short PDF
# ---------------------------------------------------------------------------


class _CloseDelimitedPdf:
    """Serves application/pdf with no length, ending by closing the connection."""

    def __init__(self, total: int | None):
        self.total = total
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        block = b"%PDF-1.4 " + b"x" * (65536 - 9)
        sent = 0
        try:
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nConnection: close\r\n\r\n"
            )
            while self.total is None or sent < self.total:
                conn.sendall(block)
                sent += len(block)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.sock.close()


def _fetch_direct_via_proxy(server: _CloseDelimitedPdf, cap: int):
    import pzi.browser_session as bs

    def connect(address, timeout=None):
        return socket.create_connection(("127.0.0.1", server.port), timeout=timeout)

    with bs.open_browser_session(
        "chromium",
        url_allowed=lambda u: True,
        max_response_bytes=cap,
        getaddrinfo=_answers(PUBLIC_IP),
        connect=connect,
    ) as session:
        return session.fetch_direct("http://pub.test/big.pdf", timeout_seconds=40)


@pytest.mark.browser
def test_fetch_direct_sees_a_failure_for_an_over_cap_close_delimited_pdf() -> None:
    from tests.browser_probe import require_browser

    require_browser()
    server = _CloseDelimitedPdf(total=None)
    try:
        result = _fetch_direct_via_proxy(server, 1024 * 1024)
    finally:
        server.close()
    assert result.status == -1 and result.body == b""
    assert not result.is_pdf()


@pytest.mark.browser
def test_fetch_direct_still_returns_a_close_delimited_pdf_within_the_cap() -> None:
    from tests.browser_probe import require_browser

    require_browser()
    server = _CloseDelimitedPdf(total=65536 * 4)
    try:
        result = _fetch_direct_via_proxy(server, 4 * 1024 * 1024)
    finally:
        server.close()
    assert result.status == 200 and result.is_pdf()
    assert len(result.body) == 65536 * 4
