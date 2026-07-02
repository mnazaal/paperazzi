"""Shared public-URL safety checks with injectable DNS resolution."""

from __future__ import annotations

import ipaddress
import queue
import socket
import threading
from collections.abc import Callable
from typing import Any, TypeAlias
from urllib.parse import urlsplit

DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS = 0.25
PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")
LOCAL_HOSTNAMES = {"localhost", "localhost.localdomain"}

ResolvedAddress: TypeAlias = tuple[Any, ...]


def safe_public_http_url(
    value: str,
    *,
    dns_timeout: float = DEFAULT_DNS_LOOKUP_TIMEOUT_SECONDS,
    resolve_host: Callable[..., list[ResolvedAddress] | None] | None = None,
    allow_host: str | None = None,
) -> bool:
    """Return True for public http(s) URL; reject localhost/private DNS/IPs.

    ``allow_host`` names a single explicitly-trusted host (e.g. a configured
    EZProxy host) whose private/campus IP is permitted.  It still must be an
    http(s) URL and is never allowed to be a bare localhost name.
    """
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False

    host = parts.hostname.strip().lower().rstrip(".")
    if _local_host_name(host):
        return False
    if allow_host and host == allow_host.strip().lower().rstrip("."):
        return True

    try:
        return public_ip_address(str(ipaddress.ip_address(host)))
    except ValueError:
        if "." not in host:
            return False
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            return False
        resolver = resolve_host or resolve_host_with_timeout
        resolved = resolver(
            host,
            port,
            timeout=dns_timeout,
        )
        if not resolved:
            return False
        return all(resolved_address_public(item) for item in resolved)


def _local_host_name(host: str) -> bool:
    return host in LOCAL_HOSTNAMES or host.endswith(PRIVATE_HOST_SUFFIXES)


def resolve_host_with_timeout(
    host: str, port: int, *, timeout: float
) -> list[ResolvedAddress] | None:
    """Resolve host with wall-clock budget; return None on timeout/error."""
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
        return None


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
