"""A capture another writer beat to the library is reported as already there.

`execute_write_plan` rebases an insert onto the library it reads under the lock,
so when a second writer commits the same DOI first, the planned insert lands as
an update. The batch totals are derived from the reported action, so a stale
action says two papers were added when the library gained one.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pzi import add_planning, add_service, bib_repository
from pzi.bib_repository import plan_bib_write, read_bib_file
from pzi.cli import run_cli
from tests.stub_translation_server import stub_translation_server, translation_item

_FIRST, _SAME = "10.1000/first", "10.1000/same"
_ITEMS = {
    _FIRST: translation_item(title="First Paper", doi=_FIRST),
    _SAME: translation_item(title="Same Paper", doi=_SAME),
}


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, front_end: str, as_json: bool
) -> tuple[int, str, Path]:
    """A two-item batch in which another writer commits item two's DOI first."""
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    # A DOI sends PDF discovery to public hosts, which conftest refuses and the
    # retries then wait out. No PDF is needed to compare totals.
    monkeypatch.setattr(add_planning, "apply_pdf_discovery", lambda record, *_a, **_k: record)
    bib = tmp_path / "library.bib"
    items = tmp_path / "items.txt"
    items.write_text(f"{_FIRST}\n{_SAME}\n")
    real_write = bib_repository.execute_write_plan
    other_writer_ran: list[bool] = []

    def write_after_the_other_writer(path: str, plan: Any, **kwargs: Any) -> Any:
        if plan["record"].get("doi") == _SAME and not other_writer_ran:
            other_writer_ran.append(True)
            current = read_bib_file(path)
            other = plan_bib_write(
                {**plan["record"], "citekey": "other2024same"},  # type: ignore[arg-type]
                current["records"],
                existing_entries=current["entries"],
            )
            assert other["action"] == "insert"
            real_write(path, other, **kwargs)
        return real_write(path, plan, **kwargs)

    monkeypatch.setattr(add_service, "execute_write_plan", write_after_the_other_writer)
    with stub_translation_server(_ITEMS) as server_url:
        config = tmp_path / "config.toml"
        config.write_text(
            f'translation_server_url = "{server_url}"\n'
            f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n'
        )
        argv = ["add", "--from-file", str(items)] if front_end == "add" else ["inbox", str(items)]
        stdout, stderr = StringIO(), StringIO()
        code = run_cli(
            [*argv, *(["--json"] if as_json else []), "--delay", "0", "--config", str(config)],
            home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
        )
    assert other_writer_ran, stderr.getvalue()
    return code, stdout.getvalue(), bib


@pytest.mark.parametrize("front_end", ["add", "inbox"])
def test_json_totals_agree_with_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, front_end: str
) -> None:
    code, out, bib = _run(tmp_path, monkeypatch, front_end=front_end, as_json=True)

    envelope = json.loads(out)
    entries = read_bib_file(str(bib))["entries"]
    assert code == 0, envelope
    assert len(entries) == 2
    assert "other2024same" in {e["citekey"] for e in entries}
    assert envelope["counts"] == {"added": 1, "exists": 1, "failed": 0}, envelope


def test_the_contended_item_reports_the_update_it_became(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _code, out, _bib = _run(tmp_path, monkeypatch, front_end="add", as_json=True)

    results = [item["result"] for item in json.loads(out)["items"]]
    assert [r["action"] for r in results] == ["insert", "update"]
    assert results[1]["citekey"] == "other2024same"
    assert "already captured" in results[1]["message"], results[1]


def test_text_summary_agrees_with_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out, bib = _run(tmp_path, monkeypatch, front_end="add", as_json=False)

    assert code == 0, out
    assert len(read_bib_file(str(bib))["entries"]) == 2
    assert "1 added" in out and "1 already" in out, out
