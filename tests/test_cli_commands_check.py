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
    base = dict(strict=False, report=None, jsonl=None, json=False, target=None,
                limit=None)
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

    It fired only under `--strict`, so `pzi library check || alert` — written straight
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
    """What `check_bib` returns when no metadata source could be reached.

    `status` is `error` with a `reason`, decided by the service — the runner
    used to re-derive that from the items, which is why `pzi.check()` reported
    the same run as clean. `test_check_verdicts` asserts the real `check_bib`
    produces this shape; here it is a stub, so keep the two in step.
    """
    return {
        "status": "error",
        "reason": "unavailable",
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
    # Built from the offline shape but with the *service's* verdict for a run
    # that did reach something: `ok`, no `reason`. Patching only
    # `sources_checked` and leaving the error verdict behind would test a
    # result the service cannot produce.
    result = _offline_result()
    result["items"][0]["sources_checked"] = ["openalex"]
    result["status"] = "ok"
    del result["reason"]

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


def test_a_partial_audit_is_reported_and_written_not_discarded(tmp_path: Path) -> None:
    """The runner must not read "one block dropped" as "could not run".

    It treated any non-ok status that way, so a duplicate citekey anywhere in
    the library meant no report file, nothing on stdout and exit 5 — after the
    audit had already made every network lookup it was going to make.
    """
    report = tmp_path / "audit.json"
    result = {**_result(), "warnings": ["not audited: duplicate citekey 'a' at line 7"]}

    code, out, err = _run(_args(report=str(report)), lambda **_k: result, tmp_path)

    # Ran fine, has something to report — not "could not run".
    assert code == exit_codes.FINDINGS
    assert report.exists(), "the completed audit was discarded"
    assert json.loads(report.read_text())["total"] == 1
    # The audited entry is shown, and the gap is named.
    assert "checked 1" in out
    assert "duplicate citekey" in err


def test_report_dash_and_jsonl_dash_together_are_refused(tmp_path: Path) -> None:
    """Three flag pairs write to stdout; two were guarded and one was not.

    `--report -` is refused with `--json` and `--jsonl -` is refused with
    `--json`, but `--report - --jsonl -` produced the whole report document
    followed by an NDJSON stream on the same stdout — neither valid JSON nor
    valid NDJSON, from the command whose `-` markers exist to be piped into jq.
    """
    called = []

    def _never(**_kw):
        called.append(True)
        return _result()

    code, out, err = _run(_args(report="-", jsonl="-"), _never, tmp_path)

    assert code == exit_codes.USAGE, err
    assert out == "", out
    assert "both write to stdout" in err
    assert called == [], "the audit ran before the invocation was refused"


def _many(count: int) -> dict:
    return {
        "status": "ok",
        "bib_name": "ml",
        "strict": False,
        "total": count,
        "counts": {"verified": count, "could_not_verify": 0, "problematic": 0},
        "items": [
            {
                "citekey": f"entry{i}",
                "verdict": "verified",
                "confidence_score": 100,
                "flags": [],
                "mismatches": [],
                "sources_checked": ["crossref"],
            }
            for i in range(count)
        ],
        "errors": [],
        "warnings": [],
    }


def _streaming_fake(count: int):
    """A `check_bib` that hands each verdict over as it is reached, as the real one does."""

    def fake(*, on_item=None, limit=None, **_kw):
        result = _many(min(count, limit) if limit else count)
        for index, item in enumerate(result["items"]):
            if on_item is not None:
                on_item(item, index, result["total"])
        return result

    return fake


def test_jsonl_is_written_as_the_audit_goes(tmp_path: Path) -> None:
    """An interrupted run has to keep the verdicts it already reached.

    `--jsonl` was built after the service returned, so a run killed at entry
    20,000 of 22,232 — hours in, on the command that takes hours — wrote
    nothing. Checked by looking at the file *during* the run, which is the only
    way to tell streaming from buffering: the finished file is identical either
    way.
    """
    out_path = tmp_path / "audit.jsonl"
    seen_midway = []

    def fake(*, on_item=None, **_kw):
        result = _many(3)
        for index, item in enumerate(result["items"]):
            on_item(item, index, 3)
            seen_midway.append(out_path.read_text(encoding="utf-8").count("\n"))
        return result

    code, _out, err = _run(_args(jsonl=str(out_path)), fake, tmp_path)

    assert code == 0, err
    assert seen_midway == [1, 2, 3], seen_midway
    assert out_path.read_text(encoding="utf-8").count("\n") == 3


def test_jsonl_is_complete_even_when_the_service_does_not_stream(tmp_path: Path) -> None:
    """The fallback: a `check_bib` that ignores `on_item` still gets a full file."""
    out_path = tmp_path / "audit.jsonl"

    code, _out, err = _run(_args(jsonl=str(out_path)), lambda **_k: _many(3), tmp_path)

    assert code == 0, err
    assert out_path.read_text(encoding="utf-8").count("\n") == 3


def test_limit_is_passed_through_and_reported(tmp_path: Path) -> None:
    """A count of an audited slice reads exactly like a count of the library."""
    code, out, err = _run(_args(limit=2), _streaming_fake(10), tmp_path)

    assert code == 0, err
    assert "checked 2" in out
    assert "--limit 2" in err
    assert "were not audited" in err


def test_limit_below_one_is_no_longer_rejected_by_the_runner(tmp_path: Path) -> None:
    """`--limit` validation moved to the parser (`cli_parser._positive_int`),
    which every real CLI invocation goes through — `library check --limit 0`
    now exits 2 with prose on stderr before this function is ever called
    (see `test_cli.py::test_library_check_limit_below_one_is_a_parser_usage_error`).

    This test calls `run_check_command` directly, bypassing the parser, which
    is exactly how the runner-level `< 1` guard this used to pin got called at
    all. That guard is deleted now that the parser is the only path a real
    invocation takes; this test documents the runner trusts its caller rather
    than re-checking, not that `limit=0` is some new valid input.
    """
    called = []

    code, out, err = _run(
        _args(limit=0), lambda **_k: called.append(True) or _many(1), tmp_path
    )

    assert called == [True]
    assert code == exit_codes.OK, err


def test_progress_is_printed_for_a_long_run_only(tmp_path: Path) -> None:
    """Nothing at all was printed until the audit finished — a hang, to a reader.

    Gated on size so a three-entry library does not grow a progress log: the
    command that needs this is the whole-library run, which is hours.
    """
    _code, _out, short_err = _run(_args(), _streaming_fake(10), tmp_path)
    assert "checked 10/10 entries" not in short_err

    _code, _out, long_err = _run(_args(), _streaming_fake(200), tmp_path)
    assert "checked 25/200 entries" in long_err
    assert "checked 200/200 entries" in long_err
    # One line per 25, not one per entry.
    assert long_err.count("entries\n") == 8, long_err
