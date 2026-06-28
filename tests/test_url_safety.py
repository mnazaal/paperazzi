import socket

from pzi import url_safety
from pzi.url_safety import (
    public_ip_address,
    resolve_host_with_timeout,
    resolved_address_public,
    safe_public_http_url,
)


def test_safe_public_http_url_uses_injected_resolver_for_hostname() -> None:
    seen: list[tuple[str, int, float]] = []

    def resolve(host: str, port: int, *, timeout: float):
        seen.append((host, port, timeout))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    assert safe_public_http_url(
        "https://Example.COM/paper.pdf", dns_timeout=0.25, resolve_host=resolve
    )
    assert seen == [("example.com", 443, 0.25)]


def test_safe_public_http_url_rejects_private_dns_result() -> None:
    def resolve(_host: str, port: int, *, timeout: float):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]

    assert not safe_public_http_url("https://example.com/paper.pdf", resolve_host=resolve)


def test_safe_public_http_url_allows_trusted_host_without_dns() -> None:
    # A configured EZProxy host short-circuits to allowed (no DNS, private OK).
    def resolve(_host: str, _port: int, *, timeout: float):
        raise AssertionError("trusted host should not be resolved")

    assert safe_public_http_url(
        "https://proxy.lib.university.edu/10.1/x",
        resolve_host=resolve,
        allow_host="proxy.lib.university.edu",
    )
    # The exception does not extend to other hosts.
    assert not safe_public_http_url(
        "https://example.com/paper.pdf",
        resolve_host=lambda _h, port, *, timeout: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))
        ],
        allow_host="proxy.lib.university.edu",
    )


def test_safe_public_http_url_rejects_local_names_without_dns() -> None:
    calls = 0

    def resolve(_host: str, _port: int, *, timeout: float):
        nonlocal calls
        calls += 1
        return []

    for value in [
        "http://localhost/file.pdf",
        "http://printer.local/file.pdf",
        "http://intranet/file.pdf",
        "file:///tmp/paper.pdf",
    ]:
        assert not safe_public_http_url(value, resolve_host=resolve)

    assert calls == 0


def test_safe_public_http_url_rejects_malformed_ports_without_dns() -> None:
    calls = 0

    def resolve(_host: str, _port: int, *, timeout: float):
        nonlocal calls
        calls += 1
        return []

    assert not safe_public_http_url("http://example.com:bad/paper.pdf", resolve_host=resolve)
    assert not safe_public_http_url("http://example.com:99999/paper.pdf", resolve_host=resolve)
    assert calls == 0


def test_safe_public_http_url_handles_direct_ip_without_dns() -> None:
    calls = 0

    def resolve(_host: str, _port: int, *, timeout: float):
        nonlocal calls
        calls += 1
        return []

    assert safe_public_http_url("https://93.184.216.34/paper.pdf", resolve_host=resolve)
    assert not safe_public_http_url("http://127.0.0.1/paper.pdf", resolve_host=resolve)
    assert calls == 0


def test_public_ip_address_rejects_non_public_ranges() -> None:
    assert public_ip_address("93.184.216.34")
    assert not public_ip_address("127.0.0.1")
    assert not public_ip_address("10.0.0.1")
    assert not public_ip_address("169.254.1.1")
    assert not public_ip_address("::1")
    assert not public_ip_address("not-an-ip")


def test_safe_public_http_url_rejects_non_http_scheme() -> None:
    assert not safe_public_http_url("ftp://example.com/x")
    assert not safe_public_http_url("https://")  # no hostname


def test_safe_public_http_url_rejects_bare_hostname_without_dot() -> None:
    # A non-IP host with no dot can't be a public FQDN; rejected before DNS.
    calls = {"n": 0}

    def resolve(host, port, *, timeout):
        calls["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    assert not safe_public_http_url("http://intranet/x", resolve_host=resolve)
    assert calls["n"] == 0


def test_safe_public_http_url_rejects_empty_dns_result() -> None:
    assert not safe_public_http_url(
        "http://example.com/x", resolve_host=lambda *a, **k: None
    )
    assert not safe_public_http_url(
        "http://example.com/x", resolve_host=lambda *a, **k: []
    )


def test_resolved_address_public_handles_malformed_entries() -> None:
    assert not resolved_address_public(("a", "b"))  # too short (<5 fields)
    assert not resolved_address_public((0, 0, 0, "", "notatuple"))  # sockaddr not tuple
    assert not resolved_address_public((0, 0, 0, "", ()))  # empty sockaddr
    assert not resolved_address_public((0, 0, 0, "", (1234, 0)))  # host not str
    assert resolved_address_public((0, 0, 0, "", ("93.184.216.34", 0)))


def test_resolve_host_with_timeout_returns_addresses(monkeypatch) -> None:
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", lambda *a, **k: fake)
    assert resolve_host_with_timeout("example.com", 443, timeout=1.0) == fake


def test_resolve_host_with_timeout_returns_none_on_error(monkeypatch) -> None:
    def _boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _boom)
    assert resolve_host_with_timeout("example.com", 443, timeout=1.0) is None
