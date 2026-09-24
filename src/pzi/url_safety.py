"""Shared public-URL safety checks with injectable DNS resolution."""

from __future__ import annotations

import ipaddress
import queue
import socket
import threading
from collections.abc import Callable, Iterable
from typing import Any, Literal, TypeAlias
from urllib.parse import urlsplit

#: 0.25s made a DNS lookup indistinguishable from a real timeout for any
#: publisher with a slower-than-instant resolver (the doi.org hop routinely
#: took longer than that under load), so a legitimate public URL was rejected
#: the same way a private one is. 2.0s gives a real resolver room to answer
#: while still bounding a single lookup to a small, fixed cost.
DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS = 2.0
PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")
LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain"}

ResolvedAddress: TypeAlias = tuple[Any, ...]

UrlClassification: TypeAlias = Literal["public", "non-public", "dns-timeout", "invalid"]


class DnsLookupTimedOut(Exception):
    """Raised by a resolver when a lookup exceeds its time budget.

    Distinct from a resolver returning ``None``/empty, which means the lookup
    *completed* and found no (or no public) address — that outcome is still
    fail-closed, but it is a different fact than "we never found out".
    """


def classify_public_http_url(
    value: str,
    *,
    dns_timeout: float = DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS,
    resolve_host: Callable[..., list[ResolvedAddress] | None] | None = None,
    allow_host: str | None = None,
) -> UrlClassification:
    """Classify *value* as a destination this process may fetch.

    ``"public"`` is the only classification a caller should treat as safe to
    fetch. ``"dns-timeout"`` is still fail-closed (never fetched) but is a
    distinct outcome from ``"non-public"`` (resolved, but private/local, or a
    malformed/non-http URL) so a caller can report *why* a URL was dropped
    instead of silently discarding it.  ``allow_host`` names a single
    explicitly-trusted host (e.g. a configured EZProxy host) whose
    private/campus IP is permitted.  It still must be an http(s) URL and is
    never allowed to be a bare localhost name.
    """
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return "invalid"
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "invalid"

    host = parts.hostname.strip().lower().rstrip(".")
    if _local_host_name(host):
        return "non-public"
    if allow_host and host == allow_host.strip().lower().rstrip("."):
        return "public"

    try:
        return "public" if public_ip_address(str(ipaddress.ip_address(host))) else "non-public"
    except ValueError:
        if "." not in host:
            return "invalid"
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            return "invalid"
        resolver = resolve_host or resolve_host_with_timeout
        try:
            resolved = resolver(host, port, timeout=dns_timeout)
        except DnsLookupTimedOut:
            return "dns-timeout"
        if not resolved:
            return "non-public"
        return (
            "public"
            if all(resolved_address_public(item) for item in resolved)
            else "non-public"
        )


def safe_public_http_url(
    value: str,
    *,
    dns_timeout: float = DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS,
    resolve_host: Callable[..., list[ResolvedAddress] | None] | None = None,
    allow_host: str | None = None,
) -> bool:
    """Return True for public http(s) URL; reject localhost/private DNS/IPs.

    Boolean wrapper around :func:`classify_public_http_url` for callers that
    only need a yes/no answer. ``allow_host`` names a single explicitly-trusted
    host (e.g. a configured EZProxy host) whose private/campus IP is
    permitted.  It still must be an http(s) URL and is never allowed to be a
    bare localhost name.
    """
    return (
        classify_public_http_url(
            value,
            dns_timeout=dns_timeout,
            resolve_host=resolve_host,
            allow_host=allow_host,
        )
        == "public"
    )


def _local_host_name(host: str) -> bool:
    return host in LOCAL_HOSTNAMES or host.endswith(PRIVATE_HOST_SUFFIXES)


def resolve_host_with_timeout(
    host: str, port: int, *, timeout: float
) -> list[ResolvedAddress] | None:
    """Resolve host with wall-clock budget.

    Returns ``None`` when resolution completes but fails (e.g. NXDOMAIN).
    Raises :class:`DnsLookupTimedOut` when the budget expires before
    resolution completes — a distinct outcome from a completed failure, so a
    caller can tell "no such host" apart from "the network was too slow to
    say" (see :func:`classify_public_http_url`).
    """
    result_queue: queue.Queue[list[ResolvedAddress] | None] = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put(list(socket.getaddrinfo(host, port)), block=False)
        except OSError:
            result_queue.put(None, block=False)

    thread = threading.Thread(target=resolve, daemon=True)
    thread.start()
    try:
        return result_queue.get(timeout=max(0.001, timeout))
    except queue.Empty:
        raise DnsLookupTimedOut(host) from None


def resolved_address_public(item: ResolvedAddress) -> bool:
    """Return True when getaddrinfo result points at public IP."""
    if len(item) < 5:
        return False
    sockaddr = item[4]
    if not isinstance(sockaddr, tuple) or not sockaddr:
        return False
    host = sockaddr[0]
    if not isinstance(host, str):
        return False
    return public_ip_address(host)


def public_ip_address(value: str) -> bool:
    """Return True for globally-routable IP address strings.

    Rejects an IPv4-mapped IPv6 literal (e.g. ``::ffff:127.0.0.1``) based on
    its embedded IPv4 address rather than the wrapper's own classification:
    on some Python patch releases (the CVE-2024-4032 window, roughly
    3.11.0-3.11.8/3.12.0-3.12.3) the IPv6 wrapper's `is_private`/`is_global`
    could disagree with the embedded address's real reachability.
    """
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # is_global already excludes private/loopback/link-local/reserved/CGNAT,
    # but (surprisingly) not multicast, so that stays an explicit check.
    return ip.is_global and not ip.is_multicast


def origin_of(url: str) -> str | None:
    """``scheme://host[:port]`` for an http(s) URL, or None.

    The one implementation: `pdf_attach_session` and `pdf_discovery` each had
    their own, identical apart from one stripping whitespace first, which is the
    behaviour kept here.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc.lower()}"


def unique_nonempty(urls: Iterable[str]) -> tuple[str, ...]:
    """*urls*, stripped, without blanks or repeats, in first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean = url.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return tuple(result)
