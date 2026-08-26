"""The `--json` envelope contract.

Every command emits the same five keys, so a consumer writes one jq path
(`.items[]`) rather than a per-command incantation, and never has to tell
"failed" apart by parsing stderr.
"""

from __future__ import annotations

import json
from io import StringIO

from pzi.cli_json import build_envelope, emit_failure, emit_result
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
