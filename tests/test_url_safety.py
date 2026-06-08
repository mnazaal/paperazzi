import socket

from pzi.url_safety import public_ip_address, safe_public_http_url


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
