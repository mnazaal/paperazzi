"""Two pzi processes writing one library at the same time.

The locking protocol was read and reasoned about single-process: every existing
concurrency test drives threads inside one interpreter, which shares the
`portalocker` handle table and so exercises a different thing from two `flock`s
held by two PIDs. The property that matters — a concurrent write is serialized
rather than lost — is only observable with real processes.

Deliberately not a timeout test. Making the 300 s lock timeout fire needs a
wedged holder, which costs the timeout to observe and tests the kernel's
semantics rather than pzi's.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_CONSOLE_SCRIPT = Path(sys.executable).parent / "pzi"

pytestmark = pytest.mark.skipif(
    not _CONSOLE_SCRIPT.exists(),
    reason="no installed `pzi` console script beside this interpreter",
)


def _library(tmp_path: Path, entries: int = 1) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".local" / "share").mkdir(parents=True)
    bib = tmp_path / "ml.bib"
    bib.write_text(
        "".join(
            f"@article{{paper{index},\n"
            f"  title = {{Paper {index}}},\n"
            f"  author = {{Author, A}},\n"
            f"  year = {{20{index:02d}}},\n"
            f"}}\n"
            for index in range(entries)
        )
    )
    papers = tmp_path / "papers"
    papers.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n'
    )
    return home, config, bib


def _run(argv: list[str], home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_CONSOLE_SCRIPT), *argv],
        capture_output=True, text=True, timeout=120,
        env={
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "PZI_SKIP_AUTO_START": "1",
        },
    )


def test_concurrent_tag_writes_from_separate_processes_all_land(
    tmp_path: Path,
) -> None:
    """Six processes tag the same entry at once; no tag may be lost.

    A lost update is what an unlocked read-modify-write produces here: each
    process reads the entry, adds its own tag, and writes the whole file back,
    so without mutual exclusion the last writer wins and the rest vanish with
    exit 0. Every process reporting success while the file holds one tag is
    precisely the failure this cannot be allowed to have.
    """
    home, config, bib = _library(tmp_path)
    tags = [f"tag{index}" for index in range(6)]

    with ThreadPoolExecutor(max_workers=len(tags)) as pool:
        results = list(pool.map(
            lambda tag: _run(
                ["tag", "add", "paper0", tag, "--config", str(config)], home
            ),
            tags,
        ))

    for tag, result in zip(tags, results):
        assert result.returncode == 0, f"{tag}: {result.stderr}"

    written = bib.read_text()
    missing = [tag for tag in tags if tag not in written]
    assert not missing, f"lost {missing} — concurrent writes clobbered each other"


def test_concurrent_inserts_from_separate_processes_all_land(
    tmp_path: Path,
) -> None:
    """The same property for whole-entry inserts, which take a different sink."""
    home, config, bib = _library(tmp_path)
    keys = [f"new{index}" for index in range(4)]

    def _insert(key: str) -> subprocess.CompletedProcess[str]:
        record = tmp_path / f"{key}.json"
        record.write_text(f'{{"title": "Paper {key}", "year": 2024}}')
        return _run(
            ["add", str(tmp_path / f"{key}.pdf"), "--citekey", key,
             "--metadata-json", str(record), "--config", str(config)],
            home,
        )

    for key in keys:
        (tmp_path / f"{key}.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        results = list(pool.map(_insert, keys))

    for key, result in zip(keys, results):
        assert result.returncode == 0, f"{key}: {result.stdout}\n{result.stderr}"

    written = bib.read_text()
    missing = [key for key in keys if key not in written]
    assert not missing, f"lost {missing} — concurrent inserts clobbered each other"
    # And the entry that was there before survived all of them.
    assert "paper0" in written


def test_a_reader_is_not_blocked_into_failure_by_a_writer(tmp_path: Path) -> None:
    """Reads take a shared lock, so they interleave with writes rather than
    failing. A read that raced a write used to be the plausible way to see a
    half-written file; the write is atomic, so it must simply see one side."""
    home, config, bib = _library(tmp_path, entries=3)

    def _work(index: int) -> subprocess.CompletedProcess[str]:
        if index % 2 == 0:
            return _run(["entries", "--json", "--config", str(config)], home)
        return _run(
            ["tag", "add", "paper1", f"t{index}", "--config", str(config)], home
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_work, range(6)))

    for index, result in enumerate(results):
        assert result.returncode == 0, f"worker {index}: {result.stderr}"
    # Every entry still present and parseable afterwards.
    assert bib.read_text().count("@article{") == 3
