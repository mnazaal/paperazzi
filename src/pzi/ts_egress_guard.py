"""Loopback forward proxy that keeps the translation-server on the public web.

The pinned Zotero translation-server fetches whatever URL it is handed with
`request` (`followAllRedirects: true`) and follows HTML meta-refresh through
the same call. It is a Node process pzi does not patch, so an up-front check on
the *initial* URL cannot see where a redirect or refresh goes next.

Every outbound HTTP(S) request from that process passes through here instead:
`HTTP_PROXY`/`HTTPS_PROXY` point at this guard, and `request` re-evaluates the
proxy for every hop. The guard resolves the destination itself, refuses any
non-public address (:func:`pzi.safe_http.resolve_pinned_ip`) with a 403, and
dials the address it validated, so a DNS answer cannot change between check and
connect. A refused hop never produces a connection to the target.

Limits: it replaces, rather than chains to, a corporate proxy the user may have
configured for the child; and a translator that opens raw sockets or spawns a
process would bypass it (none of the pinned translators' fetch paths does).
"""

from __future__ import annotations

import select
import socket
import threading
from collections.abc import Callable
from socketserver import BaseRequestHandler, ThreadingTCPServer
from typing import cast
from urllib.parse import urlsplit

from pzi.safe_http import GetAddrInfo, SsrfBlocked, resolve_pinned_ip

_MAX_HEAD_BYTES = 65536
_IO_TIMEOUT_SECONDS = 30.0
_IDLE_SECONDS = 60.0

Dial = Callable[[str, int], socket.socket]


def _dial(ip: str, port: int) -> socket.socket:
    return socket.create_connection((ip, port), timeout=15)


class _Handler(BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(_IO_TIMEOUT_SECONDS)
        try:
            head, rest = self._read_head(client)
            if head is None:
                return
            lines = head.split(b"\r\n")
            parts = lines[0].decode("latin-1").split(" ")
            if len(parts) != 3:
                return self._reply(client, 400, "bad request")
            method, target, version = parts
            if method.upper() == "CONNECT":
                host, _, port_s = target.rpartition(":")
                host = host.strip("[]")
                if not port_s.isdigit():
                    return self._reply(client, 400, "bad CONNECT target")
                upstream = self._open(client, host, int(port_s))
                if upstream is None:
                    return
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self._pump(client, upstream, rest)
                return
            url = urlsplit(target)
            if url.scheme != "http" or not url.hostname:
                return self._reply(client, 400, "only absolute http:// URLs")
            upstream = self._open(client, url.hostname, url.port or 80)
            if upstream is None:
                return
            path = (url.path or "/") + (f"?{url.query}" if url.query else "")
            kept = [
                ln for ln in lines[1:]
                if ln.split(b":", 1)[0].strip().lower()
                not in (b"proxy-connection", b"connection", b"proxy-authorization")
            ]
            out = [f"{method} {path} {version}".encode("latin-1"), *kept, b"Connection: close"]
            self._pump(client, upstream, b"\r\n".join(out) + b"\r\n\r\n" + rest)
        except (OSError, ValueError):
            return

    def _open(self, client: socket.socket, host: str, port: int) -> socket.socket | None:
        try:
            guard = cast(_GuardServer, self.server)
            ip = resolve_pinned_ip(host, port, getaddrinfo=guard.getaddrinfo)
        except SsrfBlocked as exc:
            self._reply(client, 403, str(exc))
            return None
        except OSError as exc:
            self._reply(client, 502, f"cannot resolve {host}: {exc}")
            return None
        try:
            return cast(_GuardServer, self.server).dial(ip, port)
        except OSError as exc:
            self._reply(client, 502, f"cannot connect to {host}: {exc}")
            return None

    @staticmethod
    def _read_head(client: socket.socket) -> tuple[bytes | None, bytes]:
        buf = b""
        while b"\r\n\r\n" not in buf:
            if len(buf) > _MAX_HEAD_BYTES:
                return None, b""
            chunk = client.recv(4096)
            if not chunk:
                return None, b""
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        return head, rest

    @staticmethod
    def _reply(client: socket.socket, code: int, why: str) -> None:
        body = f"pzi egress guard: {why}\n".encode("latin-1", "replace")
        reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}[code]
        try:
            client.sendall(
                f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            )
        except OSError:
            pass

    @staticmethod
    def _pump(client: socket.socket, upstream: socket.socket, first: bytes) -> None:
        try:
            if first:
                upstream.sendall(first)
            socks = [client, upstream]
            while True:
                readable, _, _ = select.select(socks, [], [], _IDLE_SECONDS)
                if not readable:
                    return
                for src in readable:
                    data = src.recv(65536)
                    if not data:
                        return
                    (upstream if src is client else client).sendall(data)
        finally:
            upstream.close()


class _GuardServer(ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, getaddrinfo: GetAddrInfo, dial: Dial) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.getaddrinfo = getaddrinfo
        self.dial = dial


class EgressGuard:
    """A running guard; `url` is what the child's `HTTP(S)_PROXY` should be."""

    def __init__(
        self, *, getaddrinfo: GetAddrInfo = socket.getaddrinfo, dial: Dial = _dial
    ) -> None:
        self._server = _GuardServer(getaddrinfo, dial)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


_shared: EgressGuard | None = None
_shared_lock = threading.Lock()


def shared_guard() -> EgressGuard:
    """One guard per pzi process; its daemon threads die with the process."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = EgressGuard()
        return _shared


def reset_shared_guard(guard: EgressGuard | None = None) -> None:
    """Replace (and close) the shared guard — tests inject a fake resolver."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
        _shared = guard


def proxy_env(guard: EgressGuard) -> dict[str, str]:
    """Child environment sending all outbound HTTP(S) through *guard*."""
    return {
        "HTTP_PROXY": guard.url, "http_proxy": guard.url,
        "HTTPS_PROXY": guard.url, "https_proxy": guard.url,
        "NO_PROXY": "", "no_proxy": "",
    }
