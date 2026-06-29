"""Tests for src/pzi/metadata_cache.py."""

from pathlib import Path

from pzi.metadata_cache import MetadataCache


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_disabled_when_ttl_zero(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, 0)
    assert cache.enabled is False
    cache.set("http://x/api", "body")
    assert cache.get("http://x/api") is None
    # nothing written
    assert not any(tmp_path.iterdir())


def test_set_then_get_roundtrip(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, 60)
    cache.set("http://x/api?q=1", "the-body")
    assert cache.get("http://x/api?q=1") == "the-body"


def test_distinct_urls_do_not_collide(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, 60)
    cache.set("http://x/api?q=1", "one")
    cache.set("http://x/api?q=2", "two")
    assert cache.get("http://x/api?q=1") == "one"
    assert cache.get("http://x/api?q=2") == "two"


def test_expiry_returns_none_and_removes_file(tmp_path: Path) -> None:
    clock = _Clock(1000.0)
    cache = MetadataCache(tmp_path, 60, clock=clock)
    cache.set("http://x/api", "body")
    assert len(list(tmp_path.iterdir())) == 1
    clock.now = 1000.0 + 61  # past TTL
    assert cache.get("http://x/api") is None
    assert not any(tmp_path.iterdir())  # expired entry pruned


def test_not_expired_within_ttl(tmp_path: Path) -> None:
    clock = _Clock(1000.0)
    cache = MetadataCache(tmp_path, 60, clock=clock)
    cache.set("http://x/api", "body")
    clock.now = 1000.0 + 59
    assert cache.get("http://x/api") == "body"


def test_corrupt_entry_is_a_miss(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, 60)
    cache.set("http://x/api", "body")
    # Corrupt the backing file.
    entry = next(tmp_path.glob("*.json"))
    entry.write_text("{not json", encoding="utf-8")
    assert cache.get("http://x/api") is None


def test_get_miss_returns_none(tmp_path: Path) -> None:
    cache = MetadataCache(tmp_path, 60)
    assert cache.get("http://never/set") is None
