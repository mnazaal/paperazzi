"""`delete --json` lists every failed citekey in the envelope's top-level `errors`."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from pzi import exit_codes
from pzi.cli import run_cli


def _delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *keys: str):
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = tmp_path / "l.bib"
    bib.write_text("@article{existing,\n title={x},\n year={2020}\n}\n")
    config = tmp_path / "c.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    out = StringIO()
    code = run_cli(
        ["delete", *keys, "--force", "--json", "--config", str(config)],
        home_dir=str(tmp_path), stdout=out, stderr=StringIO(),
    )
    return code, json.loads(out.getvalue())


def test_scalar_missing_citekey_is_in_errors(tmp_path, monkeypatch) -> None:
    code, doc = _delete(tmp_path, monkeypatch, "missing")
    assert code == exit_codes.NOT_FOUND
    assert doc["status"] == "error"
    assert any("missing" in e for e in doc["errors"])


def test_partial_batch_keeps_status_ok_and_exit_4_but_names_the_failure(
    tmp_path, monkeypatch
) -> None:
    code, doc = _delete(tmp_path, monkeypatch, "existing", "missing")
    assert code == exit_codes.PARTIAL
    assert doc["status"] == "ok"
    assert len(doc["errors"]) == 1 and "missing" in doc["errors"][0]


def test_all_failed_batch_names_every_missing_citekey(tmp_path, monkeypatch) -> None:
    code, doc = _delete(tmp_path, monkeypatch, "gone1", "gone2")
    assert code == exit_codes.NOT_FOUND
    assert doc["status"] == "error"
    joined = " ".join(doc["errors"])
    assert "gone1" in joined and "gone2" in joined


def test_fully_successful_batch_has_no_errors(tmp_path, monkeypatch) -> None:
    code, doc = _delete(tmp_path, monkeypatch, "existing")
    assert code == exit_codes.OK
    assert doc["errors"] == []


def _delete_many(tmp_path: Path, monkeypatch, bib_text: str, *keys: str, extra=()):
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = tmp_path / "l.bib"
    bib.write_text(bib_text)
    config = tmp_path / "c.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    out = StringIO()
    code = run_cli(
        ["delete", *keys, "--force", "--json", "--config", str(config), *extra],
        home_dir=str(tmp_path), stdout=out, stderr=StringIO(),
    )
    return code, json.loads(out.getvalue()), bib


_THREE = "".join(
    f"@article{{k{i},\n title={{t{i}}},\n year={{2020}}\n}}\n\n" for i in range(3)
)


def test_batch_delete_takes_one_backup_holding_the_pre_batch_library(
    tmp_path, monkeypatch
) -> None:
    code, doc, bib = _delete_many(tmp_path, monkeypatch, _THREE, "k0", "k1", "k2")
    assert code == exit_codes.OK
    backups = sorted(tmp_path.glob("l.bib.*.bak*"))
    assert len(backups) == 1
    assert backups[0].read_text() == _THREE  # restores every removal, not just one
    assert doc["deleted"] == 3
    assert [i["status"] for i in doc["items"]] == ["ok"] * 3


def test_batch_delete_backup_waits_for_the_first_real_removal(
    tmp_path, monkeypatch
) -> None:
    _, doc, _ = _delete_many(tmp_path, monkeypatch, _THREE, "nope", "k0", "k1")
    backups = sorted(tmp_path.glob("l.bib.*.bak*"))
    assert len(backups) == 1 and backups[0].read_text() == _THREE


def test_batch_delete_no_backup_still_takes_none(tmp_path, monkeypatch) -> None:
    _delete_many(tmp_path, monkeypatch, _THREE, "k0", "k1", extra=["--no-backup"])
    assert list(tmp_path.glob("*.bak*")) == []
