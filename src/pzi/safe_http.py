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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from pzi.url_safety import (
    DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS,
    classify_public_http_url,
    public_ip_address,
)

GetAddrInfo = Callable[..., Sequence[tuple[Any, ...]]]


class SsrfBlocked(urllib.error.URLError):
    """Raised when a request (or redirect) target resolves to a non-public address.

    Subclasses ``URLError`` so it surfaces as a network failure, but callers
    treat it as terminal (no retry) since re-attempting is always blocked.
    """


def _bounded_getaddrinfo(
    getaddrinfo: GetAddrInfo, host: str, port: int
) -> list[tuple[Any, ...]]:
    """Run *getaddrinfo* for at most ``DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS``.

    Same mechanism as :func:`pzi.url_safety.resolve_host_with_timeout` (a daemon
    thread and a bounded wait) and the same budget as the pre-fetch URL check,
    but with the injectable resolver and with a failed lookup re-raised as the
    ``OSError`` it was.  A lookup that outlives the budget raises
    ``socket.gaierror(EAI_AGAIN)``: a temporary resolver failure like any other,
    so the retry loop retries it as it retries a fast one, and the egress proxy
    answers 502 rather than the 403 it keeps for a blocked address.  Each attempt
    is bounded, so a retry adds at most one budget.  This bounds DNS only; see
    docs/reference.md for what bounds the rest of an outbound fetch.
    """
    outcome: list[Any] = []
    done = threading.Event()

    def lookup() -> None:
        try:
            outcome.append(list(getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except BaseException as exc:  # handed to the caller's thread below
            outcome.append(exc)
        finally:
            done.set()

    threading.Thread(target=lookup, daemon=True).start()
    if not done.wait(max(0.001, DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS)):
        raise socket.gaierror(socket.EAI_AGAIN, f"DNS lookup timed out for {host}")
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    return result


def resolve_pinned_ips(
    host: str,
    port: int,
    *,
    getaddrinfo: GetAddrInfo = socket.getaddrinfo,
    allow_host: str | None = None,
) -> list[str]:
    """Resolve *host* and return every address, rejecting if any is non-public.

    DNS failures propagate as ``OSError`` (transient, retryable).  A successful
    resolution that includes a non-public address raises :class:`SsrfBlocked`
    (terminal) — matching :func:`pzi.url_safety.safe_public_http_url`'s
    "all addresses must be public" rule.  ``allow_host`` names a single
    explicitly-trusted host (e.g. a configured EZProxy host) whose private IP
    is permitted.  Order is the resolver's, without repeats, so a caller can
    fall back to the next address the way a browser does.
    """
    infos = _bounded_getaddrinfo(getaddrinfo, host, port)
    if not infos:
        raise SsrfBlocked(f"no addresses resolved for {host}")
    trusted = bool(
        allow_host
        and host.strip().lower().rstrip(".") == allow_host.strip().lower().rstrip(".")
    )
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0] if sockaddr else ""
        if not isinstance(ip, str) or (not trusted and not public_ip_address(ip)):
            raise SsrfBlocked(f"blocked non-public address {ip!r} for host {host!r}")
        if ip not in ips:
            ips.append(ip)
    return ips


def resolve_pinned_ip(
    host: str,
    port: int,
    *,
    getaddrinfo: GetAddrInfo = socket.getaddrinfo,
    allow_host: str | None = None,
) -> str:
    """The first address of :func:`resolve_pinned_ips`."""
    return resolve_pinned_ips(host, port, getaddrinfo=getaddrinfo, allow_host=allow_host)[0]


#: Longest one address gets while others remain, so a dead first address
#: leaves time for the next within a single connect budget.
_PER_ADDRESS_CONNECT_SECONDS = 6.0

_KEEP_BUDGET = object()


def connect_first_reachable(
    ips: list[str],
    port: int,
    *,
    timeout: float,
    connect: Callable[..., socket.socket] = socket.create_connection,
    read_timeout: float | None | object = _KEEP_BUDGET,
) -> socket.socket:
    """Dial *ips* in order within one *timeout* budget; raise the last ``OSError``.

    The dial timeout is shorter than the caller's and ``create_connection``
    leaves it on the socket, so the connected socket gets ``read_timeout``
    (default: *timeout*) back before it is returned.
    """
    if read_timeout is _KEEP_BUDGET:
        read_timeout = timeout
    deadline = time.monotonic() + timeout
    last: OSError = OSError("no addresses to connect to")
    for index, ip in enumerate(ips):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        is_last = index == len(ips) - 1
        # An equal share of what is left, so addresses after a dead one are
        # still reached: each non-last attempt leaves at least its share for
        # every address behind it.
        share = remaining / (len(ips) - index)
        attempt = remaining if is_last else min(share, _PER_ADDRESS_CONNECT_SECONDS)
        try:
            sock = connect((ip, port), timeout=attempt)
        except OSError as exc:
            last = exc
            continue
        sock.settimeout(read_timeout)  # type: ignore[arg-type]
        return sock
    raise last


def _read_timeout(timeout: object) -> float | None:
    """The socket timeout a connection's ``timeout`` asks for after the dial."""
    return float(timeout) if isinstance(timeout, (int, float)) else socket.getdefaulttimeout()


def _budget(timeout: object) -> float:
    """`HTTPConnection.timeout` as a number; the default sentinel means no limit."""
    return float(timeout) if isinstance(timeout, (int, float)) else 60.0


class _PinnedHTTPConnection(http.client.HTTPConnection):
    _allow_host: str | None = None

    def connect(self) -> None:
        ips = resolve_pinned_ips(self.host, self.port, allow_host=self._allow_host)
        self.sock = connect_first_reachable(
            ips, self.port, timeout=_budget(self.timeout),
            read_timeout=_read_timeout(self.timeout),
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    _allow_host: str | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        ctx = kwargs.get("context")
        self._ssl_ctx = ctx if isinstance(ctx, ssl.SSLContext) else ssl.create_default_context()

    def connect(self) -> None:
        ips = resolve_pinned_ips(self.host, self.port, allow_host=self._allow_host)
        sock = connect_first_reachable(
            ips, self.port, timeout=_budget(self.timeout),
            read_timeout=_read_timeout(self.timeout),
        )
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


def _drop_cross_host_api_key(
    req: urllib.request.Request, new_req: urllib.request.Request, newurl: str
) -> None:
    """Strip ``x-api-key`` from *new_req* when the redirect leaves the original host.

    ``urllib``'s redirect handling copies every non-``Content-*`` header onto
    the redirected request, including a caller-supplied API key. The key is
    scoped to one host (see ``fetch_helpers._API_KEY_HOSTS``), so carrying it
    across a redirect hands it to whatever host the response named.
    """
    original_host = (urllib.parse.urlsplit(req.full_url).hostname or "").lower()
    target_host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
    if original_host == target_host:
        return
    for key in list(new_req.headers):
        if key.lower() == "x-api-key":
            del new_req.headers[key]


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
        classification = classify_public_http_url(newurl, allow_host=self._allow_host)
        if classification == "dns-timeout":
            host = urllib.parse.urlsplit(newurl).hostname or newurl
            raise SsrfBlocked(f"DNS lookup timed out for {host}")
        if classification != "public":
            raise SsrfBlocked(f"blocked redirect to non-public URL: {newurl}")
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            _drop_cross_host_api_key(req, new_req, newurl)
        return new_req


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
    *,
    context: ssl.SSLContext | None = None,
    allow_host: str | None = None,
    extra_handlers: Sequence[urllib.request.BaseHandler] = (),
) -> urllib.request.OpenerDirector:
    """Build an opener that follows redirects safely and pins resolved IPs.

    Deliberately omits proxy/file/ftp/data handlers so a redirect cannot pivot
    to a non-http(s) scheme or a local file.  ``allow_host`` permits a single
    explicitly-trusted host (e.g. a configured EZProxy host) on a private IP.
    ``extra_handlers`` adds caller-supplied handlers (e.g. a cookie processor)
    while keeping the SSRF pinning and redirect re-validation.
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
        *extra_handlers,
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
    # Reject the scheme before opening. `OpenerDirector.open` returns `None`
    # for a scheme it has no handler for, and the callers all use the result as
    # a context manager — so a `pzi-pdf-url` of `ftp://…` or `file://…` became
    # `with None as ...` → `TypeError`, caught nowhere: a traceback, no JSON,
    # and exit 1, which is `FINDINGS`. A script reads that as a clean run.
    scheme = urllib.parse.urlsplit(request.full_url).scheme.lower()
    if scheme not in ("http", "https"):
        raise SsrfBlocked(f"unsupported URL scheme {scheme or '(none)'!r}: {request.full_url}")
    opener = _SAFE_OPENER if not allow_host else build_safe_opener(allow_host=allow_host)
    return opener.open(request, timeout=timeout)
