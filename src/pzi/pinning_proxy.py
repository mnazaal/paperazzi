"""Loopback forward proxy that dials only public addresses it validated itself.

pzi hands this to two clients it cannot patch. Both resolve names and follow
redirects on their own, so an up-front check on the first URL cannot see where
they connect next:

- the Playwright browser and its request client (:mod:`pzi.browser_session`);
- the pinned Zotero translation-server, whose `request` calls use
  `followAllRedirects` and re-read `HTTP(S)_PROXY` on every hop, including HTML
  meta-refresh (:mod:`pzi.ts_egress_guard`).

Limits: it replaces, rather than chains to, a corporate proxy the user may have
configured for those clients; and a client that opens raw sockets or spawns a
process bypasses it.
"""

from __future__ import annotations

import contextlib
import selectors
import socket
import socketserver
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from pzi.safe_http import SsrfBlocked, resolve_pinned_ip

_PROXY_HEAD_LIMIT = 64 * 1024
_PROXY_IDLE_SECONDS = 60.0
_PROXY_CONNECT_SECONDS = 15.0

#: Most bytes the proxy forwards from one upstream connection to the browser.
#: Playwright's request client and Chromium buffer a whole response in memory
#: and expose no size limit, so this is where a body is bounded while it is
#: still arriving. Measured on the real path, the Node driver held about 13
#: times a body's size (264 MiB peak for a 20 MiB body), so the cap is the PDF
#: size limit of the HTTP API (64 MiB) plus headroom, not a larger figure.
#: An HTTPS tunnel is one connection however many requests it carries, so a
#: connection that has already carried this much is closed and the browser
#: reconnects.
DEFAULT_MAX_RESPONSE_BYTES = 80 * 1024 * 1024


def _valid_port(port: int) -> int:
    """Refuse a port `socket.create_connection` would raise OverflowError on."""
    if not 0 < port <= 65535:
        raise ValueError(f"port out of range: {port}")
    return port


class PinningProxy:
    """A loopback forward proxy that dials only addresses it validated.

    ``url_allowed`` checks a URL with one DNS lookup, and Chromium (or the
    Playwright request client) then resolves the name again to connect. A name
    that answers public to the first lookup and private to the second reached
    the private address with the URL check green. Handing the browser this
    proxy removes the second lookup: the browser sends the *name*, and the
    proxy resolves it once, rejects the connection if any answer is
    non-public (the rule :func:`pzi.safe_http.resolve_pinned_ip` applies to
    pzi's own fetches), and dials that exact address. HTTPS is a ``CONNECT``
    tunnel to the same pinned address, so TLS still runs end to end between
    the browser and the origin.

    Chromium does not proxy loopback destinations, but those are refused by the
    URL check before any request is made.
    """

    def __init__(
        self,
        *,
        getaddrinfo: Callable[..., Any] = socket.getaddrinfo,
        connect: Callable[..., socket.socket] = socket.create_connection,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._getaddrinfo = getaddrinfo
        self._connect = connect
        self._max_response_bytes = max_response_bytes
        proxy = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                # A client that sent bytes `latin-1` or the URL parser cannot
                # use has been answered or dropped; nothing to report.
                with contextlib.suppress(OSError, ValueError):
                    proxy._serve(self.request)

        class _Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = _Server(("127.0.0.1", 0), _Handler)
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._server.shutdown()
            self._server.server_close()

    # -- request handling ------------------------------------------------

    def _serve(self, client: socket.socket) -> None:
        client.settimeout(_PROXY_IDLE_SECONDS)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(8192)
            if not chunk or len(head) > _PROXY_HEAD_LIMIT:
                return
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3:
            self._reply(client, 400, "Bad Request")
            return
        method, target, version = parts
        try:
            if method.upper() == "CONNECT":
                host, _, port_text = target.rpartition(":")
                port = _valid_port(int(port_text))
                scheme_path = None
            else:
                split = urlsplit(target)
                if split.scheme != "http" or not split.hostname:
                    self._reply(client, 400, "Bad Request")
                    return
                host, port = split.hostname, _valid_port(split.port or 80)
                scheme_path = (split.path or "/") + (f"?{split.query}" if split.query else "")
        except ValueError:
            self._reply(client, 400, "Bad Request")
            return

        try:
            ip = resolve_pinned_ip(host.strip("[]"), port, getaddrinfo=self._getaddrinfo)
        except SsrfBlocked:
            self._reply(client, 403, "Forbidden")
            return
        except (OSError, UnicodeError):
            # `getaddrinfo` raises UnicodeError for a label IDNA cannot encode.
            self._reply(client, 502, "Bad Gateway")
            return
        try:
            upstream = self._connect((ip, port), timeout=_PROXY_CONNECT_SECONDS)
        except OSError:
            self._reply(client, 502, "Bad Gateway")
            return
        with contextlib.closing(upstream):
            upstream.settimeout(_PROXY_IDLE_SECONDS)
            if scheme_path is None:
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                pending = rest
            else:
                # One request per connection: the pinned address belongs to
                # this request's host, so a kept-alive connection must not carry
                # a second request for another one.
                kept = [
                    ln for ln in lines[1:]
                    if ln.split(":", 1)[0].strip().lower()
                    not in {"proxy-connection", "proxy-authorization", "connection"}
                ]
                kept.append("Connection: close")
                request = f"{method} {scheme_path} {version}\r\n" + "\r\n".join(kept)
                pending = request.encode("latin-1") + b"\r\n\r\n" + rest
            if pending:
                upstream.sendall(pending)
            self._relay(client, upstream, self._max_response_bytes)

    @staticmethod
    def _reply(client: socket.socket, status: int, reason: str) -> None:
        with contextlib.suppress(OSError):
            client.sendall(
                f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n"
                "Connection: close\r\n\r\n".encode()
            )

    @staticmethod
    def _relay(client: socket.socket, upstream: socket.socket, limit: int) -> None:
        a, b = client, upstream
        sent_down = 0
        sel = selectors.DefaultSelector()
        sel.register(a, selectors.EVENT_READ, b)
        sel.register(b, selectors.EVENT_READ, a)
        try:
            while True:
                events = sel.select(timeout=_PROXY_IDLE_SECONDS)
                if not events:
                    return
                for key, _ in events:
                    data = key.fileobj.recv(65536)  # type: ignore[union-attr]
                    if not data:
                        return
                    if key.fileobj is upstream:
                        room = limit - sent_down
                        if len(data) >= room:
                            # Over budget: forward what fits, then cut, so the
                            # reader sees a truncated body, never an unbounded one.
                            key.data.sendall(data[: max(room, 0)])
                            return
                        sent_down += len(data)
                    key.data.sendall(data)
        except OSError:
            return
        finally:
            sel.close()
