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

A refusal carries a ``text/plain`` body naming the guard. The translation-server
reads a reply without a ``Content-Type`` as "not in a supported format", which
hid every refusal behind one wrong message.
"""

from __future__ import annotations

import contextlib
import selectors
import socket
import socketserver
import struct
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from pzi.safe_http import SsrfBlocked, connect_first_reachable, resolve_pinned_ips

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
        self._cut_lock = threading.Lock()
        self._cut_responses = 0
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

    @property
    def cut_responses(self) -> int:
        """How many connections this proxy has seen a response cut on, or could have.

        Counts a body stopped at the byte limit and an upstream that failed, which
        includes one resetting an idle tunnel: the proxy cannot see into a tunnel
        to tell. The reset these end in does not reach every reader as a failure:
        Playwright's `page.request` runs in Node, and libuv reports data and a
        reset queued at one poll as a clean end of stream. A reader that must not
        keep a short body compares this count across its request, because a body
        with no framing of its own looks complete to it. The count goes up before
        the reset is sent, so it is current by the time the reader sees the end.
        """
        with self._cut_lock:
            return self._cut_responses

    def _note_cut(self) -> None:
        with self._cut_lock:
            self._cut_responses += 1

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
                    self._reply(client, 400, "Bad Request", "not an absolute http:// request")
                    return
                host, port = split.hostname, _valid_port(split.port or 80)
                scheme_path = (split.path or "/") + (f"?{split.query}" if split.query else "")
        except ValueError:
            self._reply(client, 400, "Bad Request", "malformed request target")
            return

        body_length = 0
        if scheme_path is not None:
            names = [ln.split(":", 1)[0].strip().lower() for ln in lines[1:]]
            if "transfer-encoding" in names:
                self._reply(
                    client, 501, "Not Implemented",
                    "chunked or encoded request bodies are not supported",
                )
                return
            lengths = [
                ln.split(":", 1)[1].strip() for ln in lines[1:]
                if ln.split(":", 1)[0].strip().lower() == "content-length"
            ]
            try:
                if len(set(lengths)) > 1:
                    raise ValueError
                body_length = int(lengths[0]) if lengths else 0
                if body_length < 0 or (lengths and not lengths[0].isdigit()):
                    raise ValueError
            except ValueError:
                self._reply(client, 400, "Bad Request", "invalid Content-Length")
                return

        try:
            ips = resolve_pinned_ips(host.strip("[]"), port, getaddrinfo=self._getaddrinfo)
        except SsrfBlocked as exc:
            self._reply(client, 403, "Forbidden", f"refused {host}: {exc}")
            return
        except (OSError, UnicodeError):
            # `getaddrinfo` raises UnicodeError for a label IDNA cannot encode.
            self._reply(client, 502, "Bad Gateway", f"could not resolve {host}")
            return
        try:
            upstream = connect_first_reachable(
                ips, port, timeout=_PROXY_CONNECT_SECONDS, connect=self._connect
            )
        except OSError:
            self._reply(client, 502, "Bad Gateway", f"could not connect to {host}:{port}")
            return
        with contextlib.closing(upstream):
            upstream.settimeout(_PROXY_IDLE_SECONDS)
            upload_budget: int | None = None
            if scheme_path is None:
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                pending = rest
            else:
                # One request per connection. The pinned address belongs to this
                # request's host, so nothing the client sends after this request
                # and its declared body is forwarded: a server that ignores
                # `Connection: close` must not receive another host's request,
                # cookies included.
                kept = [
                    ln for ln in lines[1:]
                    if ln.split(":", 1)[0].strip().lower()
                    not in {"proxy-connection", "proxy-authorization", "connection"}
                ]
                kept.append("Connection: close")
                request = f"{method} {scheme_path} {version}\r\n" + "\r\n".join(kept)
                pending = request.encode("latin-1") + b"\r\n\r\n" + rest[:body_length]
                upload_budget = max(body_length - len(rest), 0)
            upstream.sendall(pending)
            self._relay(client, upstream, self._max_response_bytes, upload_budget)

    @staticmethod
    def _reply(client: socket.socket, status: int, reason: str, detail: str = "") -> None:
        body = f"pzi egress guard: {detail or reason}\n".encode("latin-1", "replace")
        with contextlib.suppress(OSError):
            client.sendall(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )

    @staticmethod
    def _reset(sock: socket.socket) -> None:
        """Close with a TCP reset, so a cut body reads as an error, not a short one."""
        # Closed here, not left to the server: `socketserver` shuts the write
        # side down first, which sends a FIN and reads as a clean end.
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()

    def _relay(
        self,
        client: socket.socket,
        upstream: socket.socket,
        limit: int,
        upload_budget: int | None = None,
    ) -> None:
        """Copy both ways until either side closes.

        ``upload_budget`` is how many more client bytes may be forwarded: the
        unread rest of a plain-HTTP request's declared body. Once spent, the
        client is no longer read. ``None`` (a CONNECT tunnel) is unbounded.

        Every abnormal end is a reset, because a FIN reads as a complete
        close-delimited body. That holds inside a tunnel too: Node accepts a FIN
        with no TLS ``close_notify`` as a clean end.
        """
        sent_down = 0
        sel = selectors.DefaultSelector()
        sel.register(client, selectors.EVENT_READ, upstream)
        sel.register(upstream, selectors.EVENT_READ, client)
        spent = upload_budget == 0
        try:
            while True:
                events = sel.select(timeout=_PROXY_IDLE_SECONDS)
                if not events:
                    self._reset(client)
                    return
                for key, _ in events:
                    try:
                        data = key.fileobj.recv(65536)  # type: ignore[union-attr]
                    except OSError:
                        if key.fileobj is upstream:
                            self._note_cut()
                            self._reset(client)
                        return
                    if not data:
                        if spent and key.fileobj is client:
                            # A half-close after the request: the reply is
                            # still owed to it, so keep relaying downstream.
                            sel.unregister(client)
                            continue
                        return
                    if key.fileobj is upstream:
                        room = limit - sent_down
                        if len(data) >= room:
                            # Over budget: forward what fits, then reset, so the
                            # reader sees a failed body, never a short one that
                            # looks complete (a close-delimited body ends on a
                            # clean close).
                            with contextlib.suppress(OSError):
                                client.sendall(data[: max(room, 0)])
                            self._note_cut()
                            self._reset(client)
                            return
                        sent_down += len(data)
                    elif spent:
                        # The request and its declared body are done and the
                        # client sent more, so it is reusing the connection for
                        # another request, which is never forwarded. Ending it
                        # now lets the browser open a fresh one instead of
                        # waiting on a reply that cannot come.
                        self._reset(client)
                        return
                    elif upload_budget is not None:
                        data = data[:upload_budget]
                        upload_budget -= len(data)
                        spent = upload_budget == 0
                    try:
                        key.data.sendall(data)
                    except OSError:
                        # A client hanging up is routine, and says nothing about
                        # a body: browsers cancel requests all the time.
                        if key.data is upstream:
                            self._note_cut()
                        self._reset(client)
                        return
        except OSError:
            self._reset(client)
            return
        finally:
            sel.close()
