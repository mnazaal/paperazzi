"""The parse cache is only as good as its claim to return the same answer.

`pzi.parse_cache` rests on one property: `read_bib_file_raw_with_failures` is a
pure function of ``(file bytes, path)``. Every test that matters here is a way
of trying to break that — different bib shapes, a changed file, identical bytes
under two different paths — plus the miss paths, which must degrade to a parse
and never to an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pzi import parse_cache
from pzi.bib_repository import read_bib_file_raw_with_failures

# Bib shapes chosen for the things that could make a parse path-dependent,
# stateful, or lossy: a relative `file` field (resolved against the bib dir), an
# absolute one (kept as-is), a duplicate citekey (the second block is dropped, so
# `failures` is non-empty), a malformed block, and non-ASCII with brace groups.
BIB_SHAPES: dict[str, str] = {
    "plain": """
@article{smith2024graph,
  title = {Graph Parsers},
  author = {Smith, Ada},
  year = {2024},
  doi = {10.1000/foo},
}
""",
    "relative_file_field": """
@article{smith2024graph,
  title = {Graph Parsers},
  file = {papers/smith2024graph.pdf},
}
""",
    "absolute_file_field": """
@article{smith2024graph,
  title = {Graph Parsers},
  file = {/srv/papers/smith2024graph.pdf},
}
""",
    "duplicate_citekey": """
@article{dup,
  title = {First},
}

@article{dup,
  title = {Second},
}
""",
    "malformed_block": """
@article{good,
  title = {Fine},
}

@article{broken
  title = {Missing brace}
""",
    "unicode_and_braces": """
@article{muller2020,
  title = {On {BibTeX} and Na\\"{i}ve Encodings — a Sur\\'{e}te},
  author = {M{\\"u}ller, Jürgen and Łukasiewicz, Jan},
  year = {2020},
}
""",
}


@pytest.fixture(autouse=True)
def _cache_everything(monkeypatch):
    """Drop the size floor: these tests are about the cache, not about when it applies.

    `test_floor_skips_small_libraries` exercises the real constant.
    """
    monkeypatch.setattr(parse_cache, "MIN_CACHEABLE_BYTES", 0)


def _write(tmp_path: Path, text: str, name: str = "main.bib") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("shape", sorted(BIB_SHAPES))
def test_cached_read_equals_uncached_read(shape, tmp_path) -> None:
    """The property the whole design rests on, over every shape that could break it."""
    bib = _write(tmp_path, BIB_SHAPES[shape])

    cold_result, cold_failures = read_bib_file_raw_with_failures(bib)
    warm_result, warm_failures = read_bib_file_raw_with_failures(bib)

    assert warm_result == cold_result
    assert warm_failures == cold_failures


@pytest.mark.parametrize("shape", sorted(BIB_SHAPES))
def test_second_read_is_served_from_cache(shape, tmp_path, monkeypatch) -> None:
    """Not just equal — actually cached. An equal answer proves nothing on its own.

    Without this, a cache that silently never hit would pass every equality test
    above while delivering none of the speedup it exists for.
    """
    bib = _write(tmp_path, BIB_SHAPES[shape])
    read_bib_file_raw_with_failures(bib)

    def _fail(*args, **kwargs):
        raise AssertionError("parsed again instead of reading the cache")

    monkeypatch.setattr("pzi.bib_repository.parse_bibtex_with_failures", _fail)
    assert read_bib_file_raw_with_failures(bib) is not None


def test_changed_content_is_not_served_from_the_cache(tmp_path) -> None:
    """The invalidation case. Same path, different bytes, different answer."""
    bib = _write(tmp_path, BIB_SHAPES["plain"])
    first, _ = read_bib_file_raw_with_failures(bib)
    assert first["records"][0]["title"] == "Graph Parsers"

    Path(bib).write_text(
        BIB_SHAPES["plain"].replace("Graph Parsers", "Rewritten Title"), encoding="utf-8"
    )
    second, _ = read_bib_file_raw_with_failures(bib)
    assert second["records"][0]["title"] == "Rewritten Title"


def test_identical_bytes_at_two_paths_do_not_share_a_cache_entry(tmp_path) -> None:
    """`resolve_file_field` resolves a relative `file` against the bib's own directory.

    So two libraries holding byte-identical text must still read back differently,
    and a cache keyed on content alone would have handed the second the first's
    answer. This is why the key is (path, digest) and why the stored path is
    checked on load.
    """
    text = BIB_SHAPES["relative_file_field"]
    left_dir = tmp_path / "left"
    right_dir = tmp_path / "right"
    left_dir.mkdir()
    right_dir.mkdir()
    left = _write(left_dir, text)
    right = _write(right_dir, text)

    left_result, _ = read_bib_file_raw_with_failures(left)
    right_result, _ = read_bib_file_raw_with_failures(right)

    assert left_result["records"][0]["local_pdf_path"] == str(
        left_dir / "papers" / "smith2024graph.pdf"
    )
    assert right_result["records"][0]["local_pdf_path"] == str(
        right_dir / "papers" / "smith2024graph.pdf"
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda p: p.write_text("not json at all", encoding="utf-8"), id="not_json"),
        pytest.param(lambda p: p.write_text("", encoding="utf-8"), id="empty"),
        pytest.param(lambda p: p.write_text("[1, 2, 3]", encoding="utf-8"), id="not_an_object"),
        pytest.param(
            lambda p: p.write_text(json.dumps({"version": 999}), encoding="utf-8"),
            id="future_version",
        ),
        pytest.param(
            lambda p: p.write_text(
                json.dumps({"version": 1, "digest": "wrong", "bib_path": "/x"}),
                encoding="utf-8",
            ),
            id="wrong_digest",
        ),
        pytest.param(
            lambda p: p.write_bytes(b"\xff\xfe not utf-8"),
            id="not_utf8",
        ),
    ],
)
def test_corrupt_cache_file_is_a_miss_not_an_error(corrupt, tmp_path) -> None:
    """Losing the cache costs a parse. Refusing to read the library would be worse."""
    bib = _write(tmp_path, BIB_SHAPES["plain"])
    expected, _ = read_bib_file_raw_with_failures(bib)

    corrupt(parse_cache.cache_path(parse_cache.default_cache_dir(), bib))

    result, _ = read_bib_file_raw_with_failures(bib)
    assert result == expected


def test_unwritable_cache_dir_does_not_fail_the_read(tmp_path, monkeypatch) -> None:
    """A read-only or full cache directory costs a parse and nothing else."""
    bib = _write(tmp_path, BIB_SHAPES["plain"])

    def _refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr("pzi.parse_cache.tempfile.NamedTemporaryFile", _refuse)

    result, failures = read_bib_file_raw_with_failures(bib)
    assert result["records"][0]["title"] == "Graph Parsers"
    assert failures == []


def test_floor_skips_small_libraries(tmp_path, monkeypatch) -> None:
    """The real `MIN_CACHEABLE_BYTES`, which is what keeps the cache dir clean.

    The unit suite reads hundreds of small temporary bibs; caching those would
    write a file per fixture keyed by a `/tmp` path never read again, which is
    what makes pruning unnecessary rather than merely deferred.
    """
    monkeypatch.setattr(parse_cache, "MIN_CACHEABLE_BYTES", 1_000_000)
    cache_dir = Path(parse_cache.default_cache_dir())

    small = _write(tmp_path, BIB_SHAPES["plain"], name="small.bib")
    read_bib_file_raw_with_failures(small)
    assert not parse_cache.cache_path(cache_dir, small).exists()

    entry = "@article{k%d,\n  title = {%s},\n}\n"
    big_text = "".join(entry % (i, "Padding Title " * 8) for i in range(7500))
    big = _write(tmp_path, big_text, name="big.bib")
    assert len(big_text.encode("utf-8")) > parse_cache.MIN_CACHEABLE_BYTES
    read_bib_file_raw_with_failures(big)
    assert parse_cache.cache_path(cache_dir, big).exists()


def test_missing_bib_is_not_cached(tmp_path) -> None:
    """A file that does not exist returns empty before the cache is ever consulted."""
    missing = str(tmp_path / "nope.bib")
    result, failures = read_bib_file_raw_with_failures(missing)
    assert result == {"entries": [], "records": []}
    assert failures == []
    assert not parse_cache.cache_path(parse_cache.default_cache_dir(), missing).exists()
