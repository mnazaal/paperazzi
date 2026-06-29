import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi.commands.check import run_check_command


def _result(problematic=0, verified=1, cnv=0, items=None):
    return {
        "status": "ok",
        "bib_name": "main",
        "strict": False,
        "total": verified + cnv + problematic,
        "counts": {
            "verified": verified,
            "could_not_verify": cnv,
            "problematic": problematic,
        },
        "items": items
        or [
            {
                "citekey": "smith2020",
                "verdict": "verified",
                "confidence_score": 96,
                "flags": [],
                "mismatches": [],
                "sources_checked": ["crossref"],
            }
        ],
        "errors": [],
    }


def _run(args, fake, tmp_path):
    stdout, stderr = StringIO(), StringIO()
    code = run_check_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
        check_bib_fn=fake,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _args(**kw):
    base = dict(strict=False, report=None, jsonl=None, json=False, target=None)
    base.update(kw)
    return Namespace(**base)


def test_check_renders_summary(tmp_path: Path) -> None:
    code, out, _err = _run(_args(), lambda **_k: _result(), tmp_path)
    assert code == 0
    assert "verified" in out
    assert "checked 1" in out


def test_check_json_output(tmp_path: Path) -> None:
    code, out, _err = _run(_args(json=True), lambda **_k: _result(), tmp_path)
    assert code == 0
    payload = json.loads(out)
    assert payload["counts"]["verified"] == 1


def test_check_strict_exits_nonzero_on_problematic(tmp_path: Path) -> None:
    items = [
        {
            "citekey": "fake2020",
            "verdict": "problematic",
            "confidence_score": 10,
            "flags": ["chimeric"],
            "mismatches": ["author agreement only 0"],
            "sources_checked": ["crossref"],
        }
    ]
    code, _out, _err = _run(
        _args(strict=True),
        lambda **_k: _result(problematic=1, verified=0, items=items),
        tmp_path,
    )
    assert code == 1


def test_check_non_strict_problematic_still_exit_zero(tmp_path: Path) -> None:
    code, _out, _err = _run(
        _args(strict=False), lambda **_k: _result(problematic=1, verified=0), tmp_path
    )
    assert code == 0


def test_check_writes_report_and_jsonl(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    jsonl = tmp_path / "r.jsonl"
    code, _out, _err = _run(
        _args(report=str(report), jsonl=str(jsonl)), lambda **_k: _result(), tmp_path
    )
    assert code == 0
    assert json.loads(report.read_text())["total"] == 1
    lines = jsonl.read_text().strip().splitlines()
    assert json.loads(lines[0])["citekey"] == "smith2020"


def test_check_service_error_exit_one(tmp_path: Path) -> None:
    err_result = {
        "status": "error",
        "bib_name": None,
        "strict": False,
        "total": 0,
        "counts": {"verified": 0, "could_not_verify": 0, "problematic": 0},
        "items": [],
        "errors": ["no such library"],
    }
    code, _out, err = _run(_args(), lambda **_k: err_result, tmp_path)
    assert code == 1
    assert "no such library" in err
