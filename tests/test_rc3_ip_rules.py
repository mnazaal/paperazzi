"""Whether an address is public must not depend on the Python patch release."""

from __future__ import annotations

import ipaddress

import pytest

from pzi.url_safety import public_ip_address

NEVER_PUBLIC = [
    "192.0.0.0",
    "192.0.0.9",
    "192.0.0.10",
    "192.0.0.170",
    "192.0.0.254",
    "192.0.0.255",
    "2001::",
    "2001:1::1",
    "2001:1::2",
    "2001:3::1",
    "2001:4:112::1",
    "2001:20::1",
    "2001:30::1",
    "2001:1ff:ffff:ffff:ffff:ffff:ffff:ffff",
    "64:ff9b:1::1",
    "3fff::1",
    "3fff:fff:ffff:ffff:ffff:ffff:ffff:ffff",
    "5f00::1",
    "::ffff:192.0.0.254",  # IPv4-mapped
    "64:ff9b::c000:fe",  # NAT64 of 192.0.0.254
    "2001:0:4136:e378:8000:63bf:f7f7:f7f7",  # Teredo, whatever client it carries
]
PUBLIC_NEIGHBOURS = ["192.0.1.1", "8.8.8.8", "2001:200::1", "2001:4860:4860::8888", "4000::1", "2606:4700::1"]


@pytest.mark.parametrize("address", NEVER_PUBLIC)
def test_special_use_address_is_refused(address: str) -> None:
    assert public_ip_address(address) is False


@pytest.mark.parametrize("address", NEVER_PUBLIC)
def test_refused_even_when_the_standard_library_calls_it_global(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stands in for an older interpreter whose `ipaddress` tables are narrower."""
    monkeypatch.setattr(ipaddress.IPv4Address, "is_global", property(lambda self: True))
    monkeypatch.setattr(ipaddress.IPv6Address, "is_global", property(lambda self: True))
    assert public_ip_address(address) is False


@pytest.mark.parametrize("address", PUBLIC_NEIGHBOURS)
def test_public_neighbours_stay_public(address: str) -> None:
    assert public_ip_address(address) is True
