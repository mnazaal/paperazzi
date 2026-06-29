"""Tests for src/pzi/rate_limit.py."""

from pzi.rate_limit import RateLimiter


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _limiter(intervals=None):
    clock = _Clock()
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.now += seconds  # advancing time models a real sleep

    rl = RateLimiter(intervals=intervals, clock=clock, sleep=sleep)
    return rl, clock, slept


def test_first_call_to_host_does_not_sleep() -> None:
    rl, _clock, slept = _limiter({"api.example.com": 1.0})
    rl.wait("https://api.example.com/works?q=1")
    assert slept == []


def test_second_call_within_interval_sleeps_remainder() -> None:
    rl, clock, slept = _limiter({"api.example.com": 1.0})
    rl.wait("https://api.example.com/a")
    clock.now += 0.3
    rl.wait("https://api.example.com/b")
    assert slept == [0.7]


def test_call_after_interval_does_not_sleep() -> None:
    rl, clock, slept = _limiter({"api.example.com": 1.0})
    rl.wait("https://api.example.com/a")
    clock.now += 1.5
    rl.wait("https://api.example.com/b")
    assert slept == []


def test_hosts_are_independent() -> None:
    rl, _clock, slept = _limiter({"a.com": 1.0, "b.com": 1.0})
    rl.wait("https://a.com/x")
    rl.wait("https://b.com/x")  # different host: no wait
    assert slept == []


def test_longest_suffix_wins() -> None:
    rl, clock, slept = _limiter({"example.com": 5.0, "api.example.com": 1.0})
    rl.wait("https://api.example.com/a")
    clock.now += 0.1
    rl.wait("https://api.example.com/b")
    assert slept == [0.9]  # used the 1.0s rule, not the 5.0s parent


def test_unknown_host_uses_fallback_interval() -> None:
    rl, clock, slept = _limiter({})  # no configured hosts
    rl.wait("https://unknown.test/a")
    clock.now += 0.1
    rl.wait("https://unknown.test/b")
    assert slept and slept[0] > 0  # fallback spacing applied


def test_empty_host_is_noop() -> None:
    rl, _clock, slept = _limiter()
    rl.wait("not-a-url")
    assert slept == []
