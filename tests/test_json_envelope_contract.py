"""Every `--json` runner emits the documented envelope, on success and failure.

README:191 documents five keys — `command`, `status`, `bib_name`, `items`,
`errors` — as the contract a script may rely on. Eleven of fifteen runners had
no test pinning it: they were exercised by hand, and the `emit_*`/exit lines
were uncovered, so nothing would have noticed a runner that stopped emitting
one. `commands/import_.py` was the worst of them at 48 % — `run_import_command`
was never invoked by any test at all, only asserted to exist.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from pzi.cli import run_cli

ENVELOPE_KEYS = {"command", "status", "bib_name", "items", "errors"}

MINIMAL_CONFIG = """
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
"""

ENTRY = (
    "@article{smith2024,\n"
    "  title = {Deep Learning},\n"
    "  author = {Smith, John},\n"
    "  year = {2024},\n"
    "  doi = {10.1000/test},\n"
    "}\n"
)


@pytest.fixture
def library(tmp_path: Path) -> tuple[str, Path]:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")
    return str(config_path), bib_path


def _envelope(argv: list[str], tmp_path: Path) -> dict:
    stdout, stderr = StringIO(), StringIO()
    run_cli(argv, home_dir=str(tmp_path), stdout=stdout, stderr=stderr)
    raw = stdout.getvalue()
    assert raw.strip(), f"{argv} wrote no document to stdout"
    return json.loads(raw)


def _assert_envelope(envelope: dict, *, command: str) -> None:
    missing = ENVELOPE_KEYS - envelope.keys()
    assert not missing, f"{command}: envelope missing {sorted(missing)}"
    assert envelope["command"] == command
    assert envelope["status"] in {"ok", "error"}
    assert isinstance(envelope["items"], list)
    assert isinstance(envelope["errors"], list)
    if envelope["status"] == "error":
        assert envelope["errors"], f"{command}: failed with an empty errors[]"


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("entries", []),
        ("search", ["--query", "Deep"]),
        ("tag list", []),
        ("check", []),
        ("fix dedupe", []),
        ("fix clean", []),
        ("fix reindex", []),
    ],
)
def test_read_only_runners_emit_the_envelope(
    command: str, extra: list[str], library, tmp_path: Path
) -> None:
    config_path, _bib = library
    argv = [*command.split(), *extra, "--config", config_path, "--json"]
    _assert_envelope(_envelope(argv, tmp_path), command=command)


def test_import_runner_emits_the_envelope(library, tmp_path: Path) -> None:
    """`run_import_command` was never invoked by any test — 48 % coverage, and
    every success render line plus both `return OK if … else PARTIAL` uncovered.
    """
    config_path, _bib = library
    source = tmp_path / "src.bib"
    source.write_text(
        "@article{jones2023,\n  title = {Another},\n  year = {2023},\n"
        "  doi = {10.1000/other},\n}\n",
        encoding="utf-8",
    )

    envelope = _envelope(
        ["import", str(source), "--config", config_path, "--json"], tmp_path
    )
    _assert_envelope(envelope, command="import")
    assert envelope["status"] == "ok"
    assert envelope["bib_name"] == "ml"
    assert envelope["imported"] == 1


def test_import_runner_emits_the_envelope_on_failure(library, tmp_path: Path) -> None:
    config_path, _bib = library
    envelope = _envelope(
        ["import", str(tmp_path / "nope.bib"), "--config", config_path, "--json"], tmp_path
    )
    assert envelope["status"] == "error"
    assert envelope["errors"]


def test_delete_runner_emits_the_envelope(library, tmp_path: Path) -> None:
    config_path, _bib = library
    envelope = _envelope(
        ["delete", "smith2024", "--force", "--config", config_path, "--json"], tmp_path
    )
    _assert_envelope(envelope, command="delete")
    assert envelope["bib_name"] == "ml"


def test_delete_of_a_missing_entry_emits_the_envelope(library, tmp_path: Path) -> None:
    config_path, _bib = library
    envelope = _envelope(
        ["delete", "nosuch2024", "--force", "--config", config_path, "--json"], tmp_path
    )
    _assert_envelope(envelope, command="delete")
    assert envelope["status"] == "error"


def test_tag_runners_emit_the_envelope(library, tmp_path: Path) -> None:
    config_path, _bib = library
    for command in ("add", "remove"):
        envelope = _envelope(
            ["tag", command, "smith2024", "ml", "--config", config_path, "--json"],
            tmp_path,
        )
        _assert_envelope(envelope, command=f"tag {command}")


def test_fix_merge_emits_the_envelope(library, tmp_path: Path) -> None:
    config_path, bib_path = library
    bib_path.write_text(
        ENTRY + "\n@article{smith2024b,\n  title = {Deep Learning},\n"
        "  author = {Smith, John},\n  year = {2024},\n}\n",
        encoding="utf-8",
    )
    envelope = _envelope(
        ["fix", "merge", "smith2024b", "smith2024", "--config", config_path, "--json"],
        tmp_path,
    )
    _assert_envelope(envelope, command="fix merge")
    assert envelope["bib_name"] == "ml"
