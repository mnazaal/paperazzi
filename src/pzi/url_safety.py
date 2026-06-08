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
) -> bool:
    """Return True for public http(s) URL; reject localhost/private DNS/IPs."""
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False

    host = parts.hostname.strip().lower().rstrip(".")
    if _local_host_name(host):
        return False

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
    """Return True for globally-routable IP address strings."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
