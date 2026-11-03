"""Outbound rate spacing must hold across items of a batch and across retries."""

from __future__ import annotations

import io
import urllib.error

import pytest

from pzi import fetch_helpers, rate_limit
from pzi.fetch_helpers import build_metadata_fetch_text
from pzi.rate_limit import RateLimiter

S2 = "https://api.semanticscholar.org/graph/v1/paper/search?query="
CR = "https://api.crossref.org/works/"


class _World:
    """One fake clock; every sleep, from the limiter or a retry backoff, advances it."""

    def __init__(self) -> None:
        self.now = 100.0
        self.requests: list[tuple[str, float]] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Resp:
    headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, _n: int = -1) -> bytes:
        if getattr(self, "_done", False):
            return b""
        self._done = True
        return b"{}"


@pytest.fixture
def world(monkeypatch):
    w = _World()
    monkeypatch.setattr(fetch_helpers.time, "sleep", w.sleep)
    shared = RateLimiter(clock=w.clock, sleep=w.sleep)
    monkeypatch.setattr(rate_limit, "_SHARED", shared)
    return w


def _network(monkeypatch, w, failures_first: int = 0):
    state = {"failed": 0}

    def fake_urlopen(request, **_kw):
        w.requests.append((request.full_url, w.now))
        if state["failed"] < failures_first:
            state["failed"] += 1
            raise urllib.error.HTTPError(
                request.full_url, 429, "slow", {"Retry-After": "1"}, io.BytesIO(b"")  # type: ignore[arg-type]
            )
        return _Resp()

    monkeypatch.setattr(fetch_helpers, "safe_urlopen", fake_urlopen)


def test_two_items_build_separate_fetchers_but_space_the_same_host(world, monkeypatch) -> None:
    _network(monkeypatch, world)
    # Each batch item builds its own fetcher, as add_input_to_bib does.
    build_metadata_fetch_text({})(S2 + "a")
    build_metadata_fetch_text({})(S2 + "b")
    first, second = (t for _u, t in world.requests)
    assert second - first >= 6.0


def test_a_retry_obeys_the_host_interval_not_just_retry_after(world, monkeypatch) -> None:
    _network(monkeypatch, world, failures_first=1)  # Retry-After: 1s, interval 6s
    build_metadata_fetch_text({})(S2 + "a")
    (_u1, t1), (_u2, t2) = world.requests
    assert t2 - t1 >= 6.0


def test_other_hosts_are_not_spaced(world, monkeypatch) -> None:
    _network(monkeypatch, world)
    build_metadata_fetch_text({})(S2 + "a")
    build_metadata_fetch_text({})(CR + "10.1/x")
    assert [t for _u, t in world.requests] == [100.0, 100.0]


def test_cache_hits_neither_wait_nor_stamp_the_host(world, monkeypatch, tmp_path) -> None:
    _network(monkeypatch, world)
    config = {"metadata_cache_ttl": 3600, "pzi_data_home": str(tmp_path)}
    build_metadata_fetch_text(config)(S2 + "a")
    t0 = world.now
    build_metadata_fetch_text(config)(S2 + "a")  # hit: no request, no wait
    assert world.now == t0
    assert len(world.requests) == 1


def test_an_injected_fetcher_is_still_gated_per_call(world) -> None:
    calls: list[float] = []
    fetch = build_metadata_fetch_text({}, inner=lambda url, **_k: calls.append(world.now) or "{}")
    fetch(S2 + "a")
    fetch(S2 + "b")
    assert calls[1] - calls[0] >= 6.0
