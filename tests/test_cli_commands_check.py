import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import exit_codes
from pzi.check_service import check_bib
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

    # A resolving config: streaming starts only once the target resolved, which
    # is the only case in which a real audit runs at all.
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(out_path)), fake, config=config
    )

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
    assert "at most the first 2" in err


def test_the_limit_note_does_not_claim_entries_that_were_never_skipped(
    tmp_path: Path,
) -> None:
    """A library of exactly `--limit` entries was told it had unaudited entries.

    `check_bib` slices to `limit` before counting, so `result["total"]` can
    never exceed `limit` and the note's branch cannot tell "library had exactly
    N" from "library had more and was truncated". The old wording asserted the
    second — "entries after the first N were not audited" — which for a 2-entry
    library audited with `--limit 2` names unaudited entries that do not exist.
    The note now says only the part that is true either way.
    """
    code, _out, err = _run(_args(limit=2), _streaming_fake(2), tmp_path)

    assert code == 0, err
    assert "were not audited" not in err
    assert "at most the first 2" in err


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


# ── output paths that alias the library or each other ────────────────────────
#
# `check` opens `--report`/`--jsonl` before the audit runs, so every path here
# is the same shape of defect: the destination is written before anything has
# established it is not also the source. `--force` is the flag that unlocks
# overwriting, which is exactly the flag a caller uses when they mean it.


def _write_config(tmp_path: Path, bibs: list[tuple[str, Path]], *,
                  default: str | None = None) -> Path:
    lines: list[str] = []
    for name, path in bibs:
        lines += ["[[bibs]]", f'name = "{name}"', f'path = "{path}"']
        if default == name:
            lines.append("default = true")
    config = tmp_path / "config.toml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def _run_configured(tmp_path: Path, args, fake, *, selector: str | None = None,
                    config: Path | None = None):
    stdout, stderr = StringIO(), StringIO()
    code = run_check_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(config or tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=selector,
        check_bib_fn=fake,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _never_ran(**_kw):
    raise AssertionError("the audit ran before the invocation was refused")


LIBRARY = "@article{smith2020,\n  title = {A},\n  year = {2020},\n}\n"


def test_jsonl_naming_the_checked_library_is_refused(tmp_path: Path) -> None:
    """`--jsonl <the library>` truncated it, then audited the empty remainder.

    Executed through the shipped CLI: a two-entry library became 0 bytes, the
    command printed "no entries to check" and exited 0 with no backup. The
    stream is opened before the audit reads the file it is pointing at.
    """
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")

    code, out, err = _run_configured(
        tmp_path, _args(jsonl=str(bib), force=True), _never_ran, config=config
    )

    assert code == exit_codes.USAGE, err
    assert out == "", out
    assert "names the library being checked" in err
    assert bib.read_text(encoding="utf-8") == LIBRARY, "the library was rewritten"


def test_report_naming_the_checked_library_is_refused(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")

    code, _out, err = _run_configured(
        tmp_path, _args(report=str(bib), force=True), _never_ran, config=config
    )

    assert code == exit_codes.USAGE, err
    assert "names the library being checked" in err
    assert bib.read_text(encoding="utf-8") == LIBRARY


def test_output_naming_another_configured_library_is_refused(tmp_path: Path) -> None:
    """The alias that matters is the whole library set, not only the selected one.

    A default library is audited; the report is aimed at a second configured
    library. Nothing in the audit names that second file, so a guard written
    against "the target I resolved" leaves it truncatable.
    """
    main = tmp_path / "main.bib"
    other = tmp_path / "other.bib"
    main.write_text(LIBRARY, encoding="utf-8")
    other.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", main), ("other", other)], default="main")

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(other), force=True), _never_ran, config=config
    )

    assert code == exit_codes.USAGE, err
    assert "names a library" in err
    assert other.read_text(encoding="utf-8") == LIBRARY


def test_a_symlink_to_the_library_is_refused(tmp_path: Path) -> None:
    """Spelling the output as another path is not a different file."""
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    link = tmp_path / "audit.jsonl"
    link.symlink_to(bib)
    config = _write_config(tmp_path, [("main", bib)], default="main")

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(link), force=True), _never_ran, config=config
    )

    assert code == exit_codes.USAGE, err
    assert bib.read_text(encoding="utf-8") == LIBRARY


def test_a_hard_link_to_the_library_is_refused(tmp_path: Path) -> None:
    """A hard link is a second name for the same inode; realpath cannot see it."""
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    link = tmp_path / "audit.jsonl"
    link.hardlink_to(bib)
    config = _write_config(tmp_path, [("main", bib)], default="main")

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(link), force=True), _never_ran, config=config
    )

    assert code == exit_codes.USAGE, err
    assert bib.read_text(encoding="utf-8") == LIBRARY


def test_report_and_jsonl_naming_one_file_is_refused(tmp_path: Path) -> None:
    """The streamed NDJSON was replaced by the report, exit 1, no warning.

    Executed: both flags pointed at one path, so the per-entry stream written
    during the audit was overwritten by the final report. Nothing was left in
    the file that the flag promising a stream had promised.
    """
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")
    out = tmp_path / "audit.json"

    code, _out, err = _run_configured(
        tmp_path, _args(report=str(out), jsonl=str(out), force=True),
        _never_ran, config=config,
    )

    assert code == exit_codes.USAGE, err
    assert "--report and --jsonl" in err
    assert not out.exists(), "the shared output was created before the refusal"


def test_distinct_outputs_still_write_both_files(tmp_path: Path) -> None:
    """The guard must refuse collisions only; two real paths keep both artifacts."""
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")
    report = tmp_path / "audit.json"
    jsonl = tmp_path / "audit.jsonl"

    code, _out, err = _run_configured(
        tmp_path, _args(report=str(report), jsonl=str(jsonl), force=True),
        lambda **_k: _result(), config=config,
    )

    assert code == exit_codes.OK, err
    assert json.loads(report.read_text())["total"] == 1
    assert json.loads(jsonl.read_text().splitlines()[0])["citekey"] == "smith2020"


def test_an_unresolvable_config_still_reaches_the_service(tmp_path: Path) -> None:
    """No config means no input identity to compare; the service reports it.

    The guard must not invent a second failure path for a broken configuration —
    that is the service's job and its message is what the user already sees.
    """
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    out = tmp_path / "audit.json"

    code, _out, err = _run_configured(
        tmp_path, _args(report=str(out), force=True), lambda **_k: _result()
    )

    assert code == exit_codes.OK, err
    assert json.loads(out.read_text())["total"] == 1


# ── an unresolved target must not leave the alias guard blind ─────────────────


def _two_libraries(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "a.bib"
    second = tmp_path / "b.bib"
    first.write_text(LIBRARY, encoding="utf-8")
    second.write_text(LIBRARY, encoding="utf-8")
    return first, second


def test_jsonl_at_a_library_survives_an_ambiguous_target(tmp_path: Path) -> None:
    """Two libraries, no default, no `--target`: the library was wiped to 0 bytes.

    Resolution failed, so the guard compared against an empty list, and the
    stream was opened for "w" before the service reported the failure.
    """
    first, second = _two_libraries(tmp_path)
    config = _write_config(tmp_path, [("a", first), ("b", second)])

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(first), force=True), check_bib, config=config
    )

    assert code == exit_codes.ENVIRONMENT, err
    assert first.read_text(encoding="utf-8") == LIBRARY, "the library was truncated"


def test_jsonl_at_a_library_survives_an_unknown_target(tmp_path: Path) -> None:
    first, _second = _two_libraries(tmp_path)
    config = _write_config(tmp_path, [("a", first)], default="a")

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(first), force=True), check_bib,
        selector="typo", config=config,
    )

    assert code == exit_codes.ENVIRONMENT, err
    assert first.read_text(encoding="utf-8") == LIBRARY, "the library was truncated"


def test_jsonl_at_a_library_survives_a_config_that_does_not_parse(
    tmp_path: Path,
) -> None:
    first, _second = _two_libraries(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[[bibs]]\nname = "a"\npath = "{first}"\nbroken =\n', encoding="utf-8"
    )

    code, _out, err = _run_configured(
        tmp_path, _args(jsonl=str(first), force=True), check_bib, config=config
    )

    assert code == exit_codes.ENVIRONMENT, err
    assert first.read_text(encoding="utf-8") == LIBRARY, "the library was truncated"


def test_outputs_spelled_through_a_symlinked_parent_are_one_file(
    tmp_path: Path,
) -> None:
    """`L/../out.json` with `L -> elsewhere/sub` is `elsewhere/out.json`.

    `abspath` collapsed the `..` as text before `realpath` saw the symlink, so
    two spellings of one file that did not exist yet compared unequal, and the
    report overwrote the NDJSON stream.
    """
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")
    (tmp_path / "elsewhere" / "sub").mkdir(parents=True)
    link = tmp_path / "L"
    link.symlink_to(tmp_path / "elsewhere" / "sub")

    code, _out, err = _run_configured(
        tmp_path,
        _args(
            report=f"{link}/../out.json",
            jsonl=str(tmp_path / "elsewhere" / "out.json"),
            force=True,
        ),
        _never_ran, config=config,
    )

    assert code == exit_codes.USAGE, err
    assert "--report and --jsonl" in err


def test_a_zero_entry_audit_after_a_late_resolution_is_still_guarded(
    tmp_path: Path,
) -> None:
    """The post-audit guard was keyed on items, so an empty audit skipped it.

    The config gains a default between the guard and the audit; the audit
    succeeds with zero entries; `--report <that library>` then replaced the
    comment-only library with the JSON report and exited 0.
    """
    first, second = _two_libraries(tmp_path)
    first.write_text("% comment-only library\n", encoding="utf-8")
    config = _write_config(tmp_path, [("a", first), ("b", second)])

    def config_gains_a_default(**_kw):
        _write_config(tmp_path, [("a", first), ("b", second)], default="a")
        empty = _many(0)
        empty["bib_name"] = "a"
        return empty

    code, _out, err = _run_configured(
        tmp_path, _args(report=str(first), force=True), config_gains_a_default,
        config=config,
    )

    assert code == exit_codes.USAGE, err
    assert first.read_text(encoding="utf-8") == "% comment-only library\n"


def test_jsonl_to_dev_stdout_with_report_to_dash_is_one_stream(tmp_path: Path) -> None:
    """`-` is stdout; `/dev/stdout` is the same file under another name."""
    bib = tmp_path / "main.bib"
    bib.write_text(LIBRARY, encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")

    code, _out, err = _run_configured(
        tmp_path, _args(report="-", jsonl="/dev/stdout", force=True),
        _never_ran, config=config,
    )

    assert code == exit_codes.USAGE, err
    assert "--report and --jsonl" in err


def test_report_to_stdout_appended_into_the_library_is_refused(tmp_path: Path) -> None:
    """`--report - >> <library>` appended the JSON report to the library, exit 0.

    Only a real process shows this: the redirect is the process's fd 1, which a
    test's StringIO stdout is not.
    """
    import os
    import subprocess
    import sys

    import pzi

    bib = tmp_path / "main.bib"
    bib.write_text("% comment-only library\n", encoding="utf-8")
    config = _write_config(tmp_path, [("main", bib)], default="main")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PZI_SKIP_AUTO_START": "1",
        # The tree under test, not whatever `pzi` the venv's editable install
        # points at: without this the child imports the main checkout.
        "PYTHONPATH": str(Path(pzi.__file__).parents[1]),
    }
    with bib.open("a", encoding="utf-8") as appended:
        proc = subprocess.run(
            [sys.executable, "-m", "pzi", "library", "check", "--report", "-",
             "--config", str(config)],
            stdout=appended, stderr=subprocess.PIPE, text=True, env=env, timeout=120,
        )

    assert proc.returncode == exit_codes.USAGE, proc.stderr
    assert bib.read_text(encoding="utf-8") == "% comment-only library\n"
