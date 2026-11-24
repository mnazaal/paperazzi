"""The `--json` envelope contract.

Every command emits the same five keys, so a consumer writes one jq path
(`.items[]`) rather than a per-command incantation, and never has to tell
"failed" apart by parsing stderr.
"""

from __future__ import annotations

import json
from io import StringIO

from pzi.cli import run_cli
from pzi.cli_json import build_envelope, emit_failure, emit_result, merge_target_results
from pzi.errors import REASON_UNAVAILABLE


def test_envelope_normalizes_whichever_key_holds_the_list() -> None:
    # search calls it `matches`, update calls it `items`, import calls it
    # `results` — consumers should not have to know that.
    for key in ("items", "matches", "results", "bibs"):
        envelope = build_envelope({"status": "ok", key: [{"citekey": "a"}]}, command="x")
        assert envelope["items"] == [{"citekey": "a"}]
        if key != "items":
            # The original alias is consumed, not duplicated beside `items`.
            assert key not in envelope


def test_envelope_always_carries_the_five_keys() -> None:
    envelope = build_envelope({}, command="entries")
    assert envelope == {
        "command": "entries",
        "status": "ok",
        "bib_name": None,
        "items": [],
        "errors": [],
    }


def test_envelope_keeps_command_specific_fields() -> None:
    envelope = build_envelope(
        {"status": "ok", "imported": 3, "dry_run": True, "results": []},
        command="import",
    )
    assert envelope["imported"] == 3
    assert envelope["dry_run"] is True


def test_emit_result_writes_one_json_document() -> None:
    stdout = StringIO()
    emit_result(
        {"status": "error", "errors": ["boom"], "bib_name": "ml"},
        stdout,
        command="update",
    )
    payload = json.loads(stdout.getvalue())
    assert payload["command"] == "update"
    assert payload["status"] == "error"
    assert payload["errors"] == ["boom"]
    assert payload["items"] == []


def test_emit_failure_json_writes_one_document_with_reason() -> None:
    stdout = StringIO()
    stderr = StringIO()
    exit_code = emit_failure(
        "cannot write --report /no/such/dir/report.json",
        command="library check",
        reason=REASON_UNAVAILABLE,
        as_json=True,
        stdout=stdout,
        stderr=stderr,
        errors=["--report cannot be written: No such file or directory"],
    )
    # `--json` promises exactly one document on stdout; stderr must stay
    # silent on this branch — the bug `check.py`'s report-path refusal used
    # to have was printing here *and* emitting the envelope.
    assert stderr.getvalue() == ""
    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "error"
    assert payload["reason"] == REASON_UNAVAILABLE
    assert payload["errors"] == [
        "--report cannot be written: No such file or directory"
    ]
    assert exit_code == 5


def test_emit_failure_text_prints_to_stderr_not_stdout() -> None:
    stdout = StringIO()
    stderr = StringIO()
    exit_code = emit_failure(
        "Node.js is not available",
        command="doctor --reinstall-server",
        reason=REASON_UNAVAILABLE,
        as_json=False,
        stdout=stdout,
        stderr=stderr,
        errors=["Node.js is not available"],
        stderr_lines=["Node.js is not available"],
    )
    # This is the branch `doctor --reinstall-server`'s failure path used to
    # skip for two of its three call sites, printing nothing at all.
    assert stdout.getvalue() == ""
    assert "Node.js is not available" in stderr.getvalue()
    assert exit_code == 5


def test_merge_target_results_mixed_shapes_do_not_raise() -> None:
    # Every real service returns one shape per key today, so this is a
    # regression guard rather than a reachable case: `.setdefault(key,
    # []).extend(...)` on a key an earlier target already reported as a
    # scalar raised `AttributeError`, which no `cli.py` handler catches.
    merged = merge_target_results(
        [
            ("a", {"status": "ok", "summary": "first"}),
            ("b", {"status": "ok", "summary": ["second"]}),
        ],
        command="update",
    )
    # First result wins the key's shape; the mismatched second is dropped
    # rather than crashing the merge.
    assert merged["summary"] == "first"


def test_merge_sums_search_totals_and_keeps_per_target_totals() -> None:
    merged = merge_target_results(
        [
            ("a", {"status": "ok", "bib_name": "a", "matches": [{"citekey": "x"}], "total": 1}),
            ("b", {"status": "ok", "bib_name": "b", "matches": [{"citekey": "y"}], "total": 1}),
        ],
        command="search",
    )

    assert len(merged["items"]) == 2
    assert merged["total"] == 2
    assert [t["bib_name"] for t in merged["per_target"]] == ["a", "b"]
    assert [t["total"] for t in merged["per_target"]] == [1, 1]


def test_merge_sums_promotion_summary_counters_and_rederives_ratios() -> None:
    def summary(checked: int, no_candidate: int, elapsed: float, errors: int) -> dict:
        return {
            "checked": checked, "created": 1, "skipped_no_candidate": no_candidate,
            "provider_errors": errors, "elapsed_seconds": elapsed,
            "resolve_rate": 0.0, "seconds_per_candidate": 0.0,
        }

    merged = merge_target_results(
        [
            ("a", {"status": "ok", "bib_name": "a", "items": [], "summary": summary(4, 2, 8.0, 3)}),
            ("b", {"status": "ok", "bib_name": "b", "items": [], "summary": summary(6, 0, 4.0, 5)}),
        ],
        command="update --promote",
    )

    totals = merged["summary"]
    assert (totals["checked"], totals["created"], totals["provider_errors"]) == (10, 2, 8)
    assert totals["skipped_no_candidate"] == 2
    assert totals["elapsed_seconds"] == 12.0
    assert totals["resolve_rate"] == 0.8
    assert totals["seconds_per_candidate"] == 1.2
    assert [t["summary"]["provider_errors"] for t in merged["per_target"]] == [3, 5]


def test_single_target_merge_is_unchanged() -> None:
    result = {"status": "ok", "bib_name": "a", "matches": [], "total": 7,
              "summary": {"checked": 1, "resolve_rate": 0.5}}

    merged = merge_target_results([("a", result)], command="search")

    assert merged["total"] == 7
    assert merged["summary"] == result["summary"]
    assert "per_target" not in merged


def test_two_library_search_total_covers_every_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    entry = "@article{{{key},title={{Graph Parsers}},author={{Smith, Jane}},year={{2024}}}}\n"
    paths = []
    blocks = []
    for name, keys in (("a", ["a1", "a2"]), ("b", ["b1"])):
        bib = tmp_path / f"{name}.bib"
        bib.write_text("".join(entry.format(key=k) for k in keys))
        paths.append(bib)
        blocks.append(f'[[bibs]]\nname="{name}"\npath="{bib}"\n')
    config = tmp_path / "config.toml"
    config.write_text("".join(blocks))
    stdout, stderr = StringIO(), StringIO()

    code = run_cli(
        ["search", "--query", "Graph", "--target", "a", "--target", "b", "--json",
         "--config", str(config)],
        home_dir=str(tmp_path), stdout=stdout, stderr=stderr,
    )

    document = json.loads(stdout.getvalue())
    assert code == 0, stderr.getvalue()
    assert len(document["items"]) == 3
    assert document["total"] == 3
