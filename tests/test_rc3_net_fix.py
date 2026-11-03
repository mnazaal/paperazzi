"""Regression tests for the rc3 network-boundary review fixes."""

from __future__ import annotations

import pytest

from pzi.url_safety import public_ip_address


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "::127.0.0.1",  # IPv4-compatible
        "::7f00:1",
        "::ffff:127.0.0.1",  # IPv4-mapped
        "fec0::1",  # site-local
        "2002:7f00:1::",  # 6to4 of loopback
        "2002:0a00:0001::",  # 6to4 of 10.0.0.1
        "2001:0:4136:e378:8000:63bf:80ff:fffe",  # Teredo, client 127.0.0.1
        "2001:0:7f00:1::",  # Teredo with a loopback server
    ],
)
def test_ipv6_forms_carrying_private_ipv4_are_not_public(address: str) -> None:
    assert public_ip_address(address) is False


@pytest.mark.parametrize(
    "address",
    ["2606:4700:4700::1111", "2001:4860:4860::8888", "64:ff9b::808:808", "::ffff:8.8.8.8"],
)
def test_real_public_addresses_stay_public(address: str) -> None:
    assert public_ip_address(address) is True
