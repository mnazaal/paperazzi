"""The `--json` envelope contract.

Every command emits the same five keys, so a consumer writes one jq path
(`.items[]`) rather than a per-command incantation, and never has to tell
"failed" apart by parsing stderr.
"""

from __future__ import annotations

import json
from io import StringIO

from pzi.cli_json import build_envelope, emit_result


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
