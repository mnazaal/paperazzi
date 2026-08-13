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


def test_an_authenticated_lookup_does_not_read_an_anonymous_cache_entry(tmp_path) -> None:
    """The key was the URL alone, while the caller binds an api_key.

    So a Semantic Scholar lookup made without a key and the same lookup made
    with one shared an entry, and an anonymous-quota answer could be served to
    an authenticated caller for the whole TTL.
    """
    from pzi.metadata_cache import MetadataCache

    cache = MetadataCache(tmp_path / "c", ttl_seconds=600)
    cache.set("https://api.semanticscholar.org/x", '{"quota": "exceeded"}', scope="")

    assert cache.get("https://api.semanticscholar.org/x", scope="") is not None
    assert cache.get("https://api.semanticscholar.org/x", scope="secret-key\0ua") is None


def test_the_cache_is_bounded(tmp_path, monkeypatch) -> None:
    """Entries were reclaimed only by a `get()` on that exact URL, so a lookup
    never repeated was never expired and the directory grew without bound."""
    from pzi import metadata_cache

    monkeypatch.setattr(metadata_cache, "_MAX_ENTRIES", 5)
    cache = metadata_cache.MetadataCache(tmp_path / "c", ttl_seconds=600)
    for index in range(12):
        cache.set(f"https://example.org/{index}", "{}")

    assert len(list((tmp_path / "c").glob("*.json"))) <= 5
