"""A response pzi's proxy cut short must never reach `fetch_direct` as a complete one.

`page.request` runs in the Playwright driver's Node, not in the browser. libuv
reports data and a reset that are both queued at one poll as a clean end of
stream, and Node accepts a FIN with no TLS `close_notify` the same way. A body
with no framing of its own then looks complete, whatever the proxy sends.

CI hit the first case by chance. These tests force it: they stop the driver with
SIGSTOP while the proxy cuts the response, so the data and the reset are both
waiting when it next polls. libuv only reports the end after a short read, so the
frozen responses stay under its 64 KiB buffer; a queue that happens to end on a
full read gets an honest ECONNRESET instead, and proves nothing.

`page.request` reaches `http://` URLs through CONNECT as well, so `fetch_direct`
only ever uses the proxy's tunnel path. The tests below drive both schemes because
only `https://` adds TLS, whose missing `close_notify` Node also lets through.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import pzi.browser_session as bs
from pzi import pinning_proxy
from pzi.pinning_proxy import PinningProxy

PUBLIC_IP = "93.184.216.34"
CAP = 48 * 1024
SHORT_BODY = 30_000
BLOCK = b"%PDF-1.4 " + b"x" * (65536 - 9)
HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\n"

needs_a_frozen_driver = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="finds the driver through /proc"
)


def _answers(*ips: str):
    def fake(host, port, *_a, **_k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    return fake


def _driver_pid() -> int:
    """The Playwright driver: this process's one child running `node`.

    By argv, because Node renames its main thread and `comm` reads `MainThread`.
    """
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        # Any process on the machine can vanish here or carry undecodable bytes.
        with contextlib.suppress(OSError):
            stat = Path(f"/proc/{entry}/stat").read_text(errors="replace")
            argv0 = Path(f"/proc/{entry}/cmdline").read_text(errors="replace").split("\0")[0]
            ppid = int(stat[stat.rindex(")") + 2 :].split()[1])
            if ppid == os.getpid() and Path(argv0).name == "node":
                found.append(int(entry))
    assert len(found) == 1, found
    return found[0]


class _Origin:
    """Serves a PDF through whichever ending a test asks for.

    ``body`` is how much to send, ``None`` meaning until the peer hangs up.
    ``ending`` is ``close`` or ``reset``. With ``freeze_driver`` set, the driver
    stays stopped from the moment the request arrives until this connection ends,
    and the proxy has by then finished with the client.
    """

    def __init__(
        self,
        *,
        head: bytes = HEAD + b"\r\n",
        body: int | None = None,
        ending: str = "close",
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.head, self.body, self.ending, self.tls = head, body, ending, tls
        self.freeze_driver: int | None = None
        self.froze = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        pid = self.freeze_driver
        frozen = False
        try:
            conn.settimeout(10)
            if self.tls is not None:
                conn = self.tls.wrap_socket(conn, server_side=True)
            conn.recv(65536)
            if pid is not None:
                os.kill(pid, signal.SIGSTOP)
                frozen = True
                self.froze.set()
            conn.sendall(self.head)
            chunked = b"chunked" in self.head
            block = f"{len(BLOCK):x}\r\n".encode() + BLOCK + b"\r\n" if chunked else BLOCK
            sent = 0
            while self.body is None or sent < self.body:
                remaining = len(block) if self.body is None else self.body - sent
                conn.sendall(block[:remaining])
                sent += min(remaining, len(block))
            if self.ending == "reset":
                raw = socket.socket(fileno=os.dup(conn.fileno()))
                raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                raw.close()
        except OSError:
            pass
        finally:
            conn.close()
            if frozen and pid is not None:
                # The proxy's reply to the end of this connection must be in place
                # before the driver polls again.
                threading.Event().wait(0.2)
                os.kill(pid, signal.SIGCONT)

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def tls_context(tmp_path: Path) -> ssl.SSLContext:
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=pub.test", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context


def _fetch(origin: _Origin, *, frozen: bool, cap: int = CAP) -> bs.FetchResult:
    from tests.browser_probe import require_browser

    require_browser()

    def connect(address, timeout=None):
        return socket.create_connection(("127.0.0.1", origin.port), timeout=timeout)

    scheme = "https" if origin.tls is not None else "http"
    with bs.open_browser_session(
        "chromium",
        url_allowed=lambda u: True,
        max_response_bytes=cap,
        getaddrinfo=_answers(PUBLIC_IP),
        connect=connect,
    ) as session:
        if origin.tls is not None:
            # `ignore_https_errors` is for the self-signed certificate only.
            browser, _ = session.browser_ref
            session.page = browser.new_context(ignore_https_errors=True).new_page()
        if frozen:
            origin.freeze_driver = _driver_pid()
        result = session.fetch_direct(f"{scheme}://pub.test/big.pdf", timeout_seconds=40)
    if frozen:
        assert origin.froze.is_set()
    return result


# ---------------------------------------------------------------------------
# Real driver: the ending a truncated body used to slip through
# ---------------------------------------------------------------------------


@pytest.mark.browser
@needs_a_frozen_driver
def test_fetch_direct_fails_an_http_body_cut_at_the_cap_while_the_driver_is_busy() -> None:
    origin = _Origin()
    try:
        result = _fetch(origin, frozen=True)
    finally:
        origin.close()
    assert (result.status, result.body) == (-1, b"")


@pytest.mark.browser
@needs_a_frozen_driver
def test_fetch_direct_fails_an_https_body_cut_at_the_cap_while_the_driver_is_busy(
    tls_context: ssl.SSLContext,
) -> None:
    origin = _Origin(tls=tls_context)
    try:
        result = _fetch(origin, frozen=True)
    finally:
        origin.close()
    assert (result.status, result.body) == (-1, b"")


@pytest.mark.browser
@needs_a_frozen_driver
def test_fetch_direct_fails_when_the_origin_resets_an_http_body_while_the_driver_is_busy() -> None:
    origin = _Origin(body=SHORT_BODY, ending="reset")
    try:
        result = _fetch(origin, frozen=True, cap=CAP * 16)
    finally:
        origin.close()
    assert (result.status, result.body) == (-1, b"")


@pytest.mark.browser
def test_fetch_direct_fails_when_the_origin_resets_an_https_body(
    tls_context: ssl.SSLContext,
) -> None:
    """Needs no frozen driver: the proxy used to end this with a FIN every time.

    Long enough that the driver has the response head before the origin's reset
    discards what it still has queued. With less, there is no short body to keep.
    """
    origin = _Origin(body=len(BLOCK) * 2, ending="reset", tls=tls_context)
    try:
        result = _fetch(origin, frozen=False, cap=CAP * 16)
    finally:
        origin.close()
    assert (result.status, result.body) == (-1, b"")


@pytest.mark.browser
@needs_a_frozen_driver
@pytest.mark.parametrize(
    "framing", ["Content-Length: 104857600\r\n", "Transfer-Encoding: chunked\r\n"]
)
def test_a_framed_body_cut_while_the_driver_is_busy_fails_on_its_framing_alone(
    framing: str,
) -> None:
    """`fetch_direct` trusts framing and skips the proxy's count for these responses."""
    head = HEAD + framing.encode() + b"\r\n"
    origin = _Origin(head=head)
    try:
        result = _fetch(origin, frozen=True)
    finally:
        origin.close()
    assert (result.status, result.body) == (-1, b"")


@pytest.mark.browser
@needs_a_frozen_driver
def test_a_complete_close_delimited_body_still_arrives_with_the_driver_frozen() -> None:
    """The freeze alone fails nothing, so the tests above measure the cut."""
    origin = _Origin(body=SHORT_BODY)
    try:
        result = _fetch(origin, frozen=True, cap=CAP * 16)
    finally:
        origin.close()
    assert result.status == 200 and result.is_pdf()
    assert len(result.body) == SHORT_BODY


@pytest.mark.browser
def test_a_complete_https_close_delimited_body_still_arrives(
    tls_context: ssl.SSLContext,
) -> None:
    origin = _Origin(body=len(BLOCK) * 2, tls=tls_context)
    try:
        result = _fetch(origin, frozen=False, cap=CAP * 16)
    finally:
        origin.close()
    assert result.status == 200 and len(result.body) == len(BLOCK) * 2


# ---------------------------------------------------------------------------
# The proxy: which endings it counts, and how it ends a connection
# ---------------------------------------------------------------------------


def _read_until_end(sock: socket.socket) -> tuple[bytes, OSError | None]:
    data = b""
    try:
        while chunk := sock.recv(65536):
            data += chunk
    except OSError as exc:
        return data, exc
    return data, None


def _proxy_for(origin: _Origin, **kwargs: Any) -> PinningProxy:
    def connect(address, timeout=None):
        return socket.create_connection(("127.0.0.1", origin.port), timeout=timeout)

    return PinningProxy(getaddrinfo=_answers(PUBLIC_IP), connect=connect, **kwargs)


def _plain_exchange(proxy: PinningProxy) -> OSError | None:
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
        s.sendall(b"GET http://pub.test/a.pdf HTTP/1.1\r\nHost: pub.test\r\n\r\n")
        return _read_until_end(s)[1]


def _tunnel_exchange(proxy: PinningProxy) -> OSError | None:
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
        s.sendall(b"CONNECT pub.test:443 HTTP/1.1\r\nHost: pub.test:443\r\n\r\n")
        assert b"200" in s.recv(4096)
        s.sendall(b"hello")
        return _read_until_end(s)[1]


def _settled_cuts(proxy: PinningProxy) -> int:
    """The count, once the proxy's thread for a just-finished exchange has ended."""
    for thread in threading.enumerate():
        if "process_request_thread" in thread.name:
            thread.join(5)
    return proxy.cut_responses


@pytest.mark.parametrize("exchange", [_plain_exchange, _tunnel_exchange])
def test_a_body_over_the_cap_is_counted_and_ends_in_a_reset(exchange) -> None:
    origin = _Origin()
    proxy = _proxy_for(origin, max_response_bytes=CAP)
    try:
        error = exchange(proxy)
        assert isinstance(error, ConnectionResetError)
        assert _settled_cuts(proxy) == 1
    finally:
        proxy.close()
        origin.close()


@pytest.mark.parametrize("exchange", [_plain_exchange, _tunnel_exchange])
def test_an_origin_reset_is_counted_and_ends_in_a_reset(exchange) -> None:
    origin = _Origin(body=len(BLOCK), ending="reset")
    proxy = _proxy_for(origin)
    try:
        error = exchange(proxy)
        assert isinstance(error, ConnectionResetError)
        assert _settled_cuts(proxy) == 1
    finally:
        proxy.close()
        origin.close()


@pytest.mark.parametrize("exchange", [_plain_exchange, _tunnel_exchange])
def test_an_origin_closing_normally_is_not_a_cut(exchange) -> None:
    origin = _Origin(body=len(BLOCK))
    proxy = _proxy_for(origin)
    try:
        assert exchange(proxy) is None
        assert _settled_cuts(proxy) == 0
    finally:
        proxy.close()
        origin.close()


def test_a_client_hanging_up_is_not_a_cut() -> None:
    """Browsers cancel requests all the time, and that says nothing about a body."""
    origin = _Origin()
    proxy = _proxy_for(origin)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(b"GET http://pub.test/a.pdf HTTP/1.1\r\nHost: pub.test\r\n\r\n")
            s.recv(65536)
        assert _settled_cuts(proxy) == 0
    finally:
        proxy.close()
        origin.close()


def test_a_stalled_tunnel_ends_in_a_reset_without_being_counted(monkeypatch) -> None:
    """A stalled connection is often just an idle keep-alive one, so it is not a cut."""
    monkeypatch.setattr(pinning_proxy, "_PROXY_IDLE_SECONDS", 2.0)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def connect(address, timeout=None):
        return socket.create_connection(listener.getsockname(), timeout=timeout)

    proxy = PinningProxy(getaddrinfo=_answers(PUBLIC_IP), connect=connect)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(b"CONNECT pub.test:443 HTTP/1.1\r\nHost: pub.test:443\r\n\r\n")
            assert b"200" in s.recv(4096)
            assert isinstance(_read_until_end(s)[1], ConnectionResetError)
        assert _settled_cuts(proxy) == 0
    finally:
        proxy.close()
        listener.close()


class _FailsAfterFirstSend(socket.socket):
    """An upstream socket whose first send (the request) works and the next fails."""

    sends = 0

    def sendall(self, *args: Any, **kwargs: Any) -> None:
        self.sends += 1
        if self.sends > 1:
            raise BrokenPipeError
        super().sendall(*args, **kwargs)


def test_an_origin_failing_while_the_client_sends_to_it_is_counted() -> None:
    far_end, near_end = socket.socketpair()
    upstream = _FailsAfterFirstSend(socket.AF_UNIX, socket.SOCK_STREAM, fileno=near_end.detach())
    proxy = PinningProxy(
        getaddrinfo=_answers(PUBLIC_IP), connect=lambda *a, **k: upstream
    )
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=10) as s:
            s.sendall(b"CONNECT pub.test:443 HTTP/1.1\r\nHost: pub.test:443\r\n\r\nfirst")
            assert b"200" in s.recv(4096)
            s.sendall(b"second")
            assert isinstance(_read_until_end(s)[1], ConnectionResetError)
        assert _settled_cuts(proxy) == 1
    finally:
        proxy.close()
        far_end.close()


# ---------------------------------------------------------------------------
# fetch_direct: what it does with the count
# ---------------------------------------------------------------------------


class _CuttingProxy:
    """Counts a cut during each request when ``cuts`` is set."""

    def __init__(self, cuts: bool) -> None:
        self.cuts = cuts
        self.cut_responses = 7

    def close(self) -> None:
        pass


def _session_answering(headers: dict[str, str], *, cuts: bool) -> bs.BrowserSession:
    proxy = _CuttingProxy(cuts)

    def get(url: str, **_kwargs: Any) -> SimpleNamespace:
        if proxy.cuts:
            proxy.cut_responses += 1
        return SimpleNamespace(status=200, headers=headers, body=lambda: b"%PDF-1.4 whole")

    session = bs.BrowserSession(
        playwright=None,
        browser_ref=None,
        page=SimpleNamespace(request=SimpleNamespace(get=get)),
        url_allowed=lambda u: True,
    )
    session._proxy = proxy  # type: ignore[assignment]
    return session


PDF_TYPE = {"content-type": "application/pdf"}


@pytest.mark.parametrize(
    "headers",
    [
        PDF_TYPE,
        {**PDF_TYPE, "transfer-encoding": "gzip"},
        {**PDF_TYPE, "transfer-encoding": "chunked, identity"},
        {**PDF_TYPE, "transfer-encoding": "xchunked"},
    ],
    ids=["no-framing", "transfer-encoding-without-chunked", "chunked-not-last", "not-quite-chunked"],
)
def test_a_cut_during_a_close_delimited_response_fails_the_fetch(headers) -> None:
    result = _session_answering(headers, cuts=True).fetch_direct("http://pub.test/a.pdf")
    assert (result.status, result.body) == (-1, b"")


@pytest.mark.parametrize(
    "framing",
    [{"content-length": "14"}, {"transfer-encoding": "chunked"}, {"transfer-encoding": "gzip, Chunked"}],
    ids=["content-length", "chunked", "chunked-after-another-coding"],
)
def test_another_connections_cut_does_not_fail_a_framed_response(framing) -> None:
    """A tracker on the open page being reset must not lose a good PDF."""
    result = _session_answering({**PDF_TYPE, **framing}, cuts=True).fetch_direct(
        "http://pub.test/a.pdf"
    )
    assert result.status == 200 and result.body == b"%PDF-1.4 whole"


def test_a_close_delimited_response_with_no_cut_is_kept() -> None:
    result = _session_answering(PDF_TYPE, cuts=False).fetch_direct("http://pub.test/a.pdf")
    assert result.status == 200 and result.body == b"%PDF-1.4 whole"
