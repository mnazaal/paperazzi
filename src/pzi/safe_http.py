"""SSRF-hardened HTTP opener: validates redirects and pins resolved IPs.

``urllib`` follows redirects and re-resolves DNS independently of any up-front
URL check, which leaves two SSRF gaps:

1. A validated public URL can ``302`` redirect to an internal target
   (``127.0.0.1``, ``169.254.169.254`` cloud metadata, RFC1918 hosts).
2. A hostname can resolve to a public IP at validation time but a private IP at
   connect time (DNS rebinding) — the check and the socket connect race.

This module closes both: every redirect target is re-validated, and each
connection resolves the host once and connects to *that* validated address,
rejecting any non-public IP.  The IP that is checked is the IP that is dialed,
so there is no check/connect gap.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from pzi.url_safety import public_ip_address, safe_public_http_url

GetAddrInfo = Callable[..., Sequence[tuple[Any, ...]]]


class SsrfBlocked(urllib.error.URLError):
    """Raised when a request (or redirect) target resolves to a non-public address.

    Subclasses ``URLError`` so it surfaces as a network failure, but callers
    treat it as terminal (no retry) since re-attempting is always blocked.
    """


def resolve_pinned_ip(
    host: str,
    port: int,
    *,
    getaddrinfo: GetAddrInfo = socket.getaddrinfo,
    allow_host: str | None = None,
) -> str:
    """Resolve *host* and return one address, rejecting if any is non-public.

    DNS failures propagate as ``OSError`` (transient, retryable).  A successful
    resolution that includes a non-public address raises :class:`SsrfBlocked`
    (terminal) — matching :func:`pzi.url_safety.safe_public_http_url`'s
    "all addresses must be public" rule.  ``allow_host`` names a single
    explicitly-trusted host (e.g. a configured EZProxy host) whose private IP
    is permitted.
    """
    infos = list(getaddrinfo(host, port, type=socket.SOCK_STREAM))
    if not infos:
        raise SsrfBlocked(f"no addresses resolved for {host}")
    trusted = bool(
        allow_host
        and host.strip().lower().rstrip(".") == allow_host.strip().lower().rstrip(".")
    )
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0] if sockaddr else ""
        if not isinstance(ip, str) or (not trusted and not public_ip_address(ip)):
            raise SsrfBlocked(f"blocked non-public address {ip!r} for host {host!r}")
    return infos[0][4][0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    _allow_host: str | None = None

    def connect(self) -> None:
        ip = resolve_pinned_ip(self.host, self.port, allow_host=self._allow_host)
        self.sock = socket.create_connection((ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    _allow_host: str | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        ctx = kwargs.get("context")
        self._ssl_ctx = ctx if isinstance(ctx, ssl.SSLContext) else ssl.create_default_context()

    def connect(self) -> None:
        ip = resolve_pinned_ip(self.host, self.port, allow_host=self._allow_host)
        sock = socket.create_connection((ip, self.port), self.timeout)
        # Dial the pinned IP, but verify TLS against the hostname (SNI + cert).
        self.sock = self._ssl_ctx.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, conn_cls: type[http.client.HTTPConnection]) -> None:
        super().__init__()
        self._conn_cls = conn_cls

    def http_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(self._conn_cls, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self, context: ssl.SSLContext, conn_cls: type[http.client.HTTPSConnection]
    ) -> None:
        super().__init__(context=context)
        self._ssl_ctx = context
        self._conn_cls = conn_cls

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(self._conn_cls, req, context=self._ssl_ctx)


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_host: str | None = None) -> None:
        self._allow_host = allow_host

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not safe_public_http_url(newurl, allow_host=self._allow_host):
            raise SsrfBlocked(f"blocked redirect to non-public URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _pinned_conn_classes(
    allow_host: str | None,
) -> tuple[type[http.client.HTTPConnection], type[http.client.HTTPSConnection]]:
    """Return connection subclasses bound to *allow_host* (or the base classes)."""
    if not allow_host:
        return _PinnedHTTPConnection, _PinnedHTTPSConnection

    class _HTTP(_PinnedHTTPConnection):
        _allow_host = allow_host

    class _HTTPS(_PinnedHTTPSConnection):
        _allow_host = allow_host

    return _HTTP, _HTTPS


def build_safe_opener(
    *, context: ssl.SSLContext | None = None, allow_host: str | None = None
) -> urllib.request.OpenerDirector:
    """Build an opener that follows redirects safely and pins resolved IPs.

    Deliberately omits proxy/file/ftp/data handlers so a redirect cannot pivot
    to a non-http(s) scheme or a local file.  ``allow_host`` permits a single
    explicitly-trusted host (e.g. a configured EZProxy host) on a private IP.
    """
    ctx = context or ssl.create_default_context()
    http_cls, https_cls = _pinned_conn_classes(allow_host)
    opener = urllib.request.OpenerDirector()
    for handler in (
        _PinnedHTTPHandler(http_cls),
        _PinnedHTTPSHandler(ctx, https_cls),
        _ValidatingRedirectHandler(allow_host),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPDefaultErrorHandler(),
    ):
        opener.add_handler(handler)
    return opener


_SAFE_OPENER = build_safe_opener()


def safe_urlopen(
    request: urllib.request.Request, *, timeout: float, allow_host: str | None = None
) -> Any:
    """Open *request* with redirect re-validation and connect-time IP pinning.

    ``allow_host`` permits a single explicitly-trusted host on a private IP
    (configured EZProxy); without it the cached strict opener is used.
    """
    opener = _SAFE_OPENER if not allow_host else build_safe_opener(allow_host=allow_host)
    return opener.open(request, timeout=timeout)
