import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import exit_codes
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


def test_check_reports_a_problematic_entry_without_strict(tmp_path: Path) -> None:
    """`1` is the documented answer for "ran fine and has something to report".

    It fired only under `--strict`, so `pzi check || alert` — written straight
    from the README's exit-code table — stayed silent on a library of fabricated
    references. `--strict` selects *harder checks*; it is not the switch that
    decides whether a finding is reported.
    """
    code, _out, _err = _run(
        _args(strict=False), lambda **_k: _result(problematic=1, verified=0), tmp_path
    )
    assert code == 1


def test_check_reports_an_entry_it_could_not_verify(tmp_path: Path) -> None:
    """The README names this case explicitly; it never fired in either mode."""
    code, _out, _err = _run(
        _args(strict=False), lambda **_k: _result(cnv=1, verified=0), tmp_path
    )
    assert code == 1


def test_check_exits_zero_when_every_entry_verified(tmp_path: Path) -> None:
    code, _out, _err = _run(
        _args(strict=False), lambda **_k: _result(verified=2), tmp_path
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


def test_check_service_error_exits_environment(tmp_path: Path) -> None:
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
    assert code == exit_codes.ENVIRONMENT
    assert "no such library" in err


def _offline_result():
    """What `check_bib` returns when no metadata source could be reached."""
    return {
        "status": "ok",
        "bib_name": "main",
        "strict": False,
        "total": 1,
        "counts": {"verified": 0, "could_not_verify": 1, "problematic": 0},
        "items": [
            {
                "citekey": "smith2020",
                "verdict": "could_not_verify",
                "confidence_score": 0,
                "flags": [],
                "mismatches": ["no source could be reached (see source_errors)"],
                "sources_checked": [],
                "source_errors": ["crossref: connection refused"],
            }
        ],
        "errors": ["crossref: unreachable for some or all entries"],
    }


def test_check_reports_unreachable_sources_on_stderr(tmp_path):
    code, _stdout, stderr = _run(
        _args(), lambda **_kw: _offline_result(), tmp_path
    )

    assert "metadata sources unavailable" in stderr
    assert "crossref" in stderr
    # Nothing was audited, so a clean exit would misreport the library.
    assert code == exit_codes.ENVIRONMENT


def test_check_reports_findings_not_failure_when_some_source_answered(tmp_path):
    """A partly-unreachable run still audited something, so it is not ENVIRONMENT.

    The entry it could not verify is a finding, which is `1`.
    """
    result = _offline_result()
    result["items"][0]["sources_checked"] = ["openalex"]

    code, _stdout, stderr = _run(_args(), lambda **_kw: result, tmp_path)

    assert "metadata sources unavailable" in stderr
    assert code == exit_codes.FINDINGS



def test_check_refuses_an_unwritable_report_path_before_auditing(tmp_path) -> None:
    """`check` is the long, network-bound command.

    The report was opened only after the whole audit had run, so an unwritable
    path threw away work that could take minutes. `add` fail-fasts
    `--metadata-json` for this exact reason.
    """
    from io import StringIO

    from pzi.commands.check import run_check_command

    config_path = tmp_path / "config.toml"
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{a2024,\n  title = {A},\n}\n")
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n'
    )

    audited = []

    def _never_runs(**kwargs):
        audited.append(kwargs)
        raise AssertionError("the audit ran before the report path was checked")

    args = type("A", (), {
        "report": str(tmp_path / "no-such-dir" / "report.json"),
        "jsonl": None, "json": False, "strict": False, "target": None,
    })()

    stdout, stderr = StringIO(), StringIO()
    code = run_check_command(
        args,
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        stdout=stdout,
        stderr=stderr,
        check_bib_fn=_never_runs,
    )

    assert audited == [], "the audit should not have started"
    assert code != 0
    assert "--report" in stderr.getvalue()
