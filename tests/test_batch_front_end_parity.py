"""The three batch front ends, compared to each other — audit items C3 and C4.

`add --from-file`, `inbox` and `import` are three ways to put many records into
a library in one command. Each was fixed in isolation and neither fix reached
the siblings, which is the defect shape this file exists to close:

* **C3 — the status a wholly-failed batch reports.** `add --from-file` derives
  it (`commands/add.py`): a batch that captured nothing is `status: "error"`,
  a batch that captured something is `"ok"` however many items failed, because
  the exit code already carries partial-ness. `inbox_service` and
  `import_service` hardcoded `"status": "ok"` and so contradicted their own
  non-zero exit.
* **C4 — whether a preview sees its own earlier items.** `import` holds one
  `batch_write_session` and applies each plan to it even in dry-run, so record
  K is matched against the library *plus* records 1..K-1. `inbox` and
  `add --from-file` call the single-capture path once per item, and that path
  re-reads the library from disk every time — so in dry-run each item was blind
  to the rest of its own batch. Two inputs for one paper previewed as two
  inserts where the real run inserts then updates.

Both properties are asserted across every front end at once. A fix that reaches
one call site fails this file.

**Two facts the envelopes force on this file.** Each front end spells a
per-item outcome differently — `import` uses `action`, `add` nests one under
`result`, `inbox` reports `added`/`exists` and carries no action at all — so
`_outcomes` normalizes before comparing. And `parse_batch_values` de-duplicates
identical lines, so exercising C4 through `add --from-file` needs two *distinct*
strings naming one paper: a DOI and its `https://doi.org/` form.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pzi import exit_codes
from tests.stub_translation_server import stub_translation_server, translation_item

_DOI = "10.1145/3372297"
_DOI_URL = f"https://doi.org/{_DOI}"
_OTHER_DOI = "10.1145/3372298"


def _write_config(tmp_path: Path, *, extra: str = "") -> Path:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("")
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'{extra}[[bibs]]\nname = "ml"\npath = "{bib_path}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n'
    )
    return config_path


def _run_cli(argv: list[str], *, home: Path, timeout: int = 180):
    env = {**os.environ, "HOME": str(home), "PZI_SKIP_AUTO_START": "1"}
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; from pzi.cli import run_cli; sys.exit(run_cli(sys.argv[1:]))",
         *argv],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


def _stub_paper(title: str = "A Stub Paper") -> dict:
    """One paper the stub answers for under either of its two identifiers."""
    return {
        _DOI: translation_item(title=title),
        _DOI_URL: translation_item(title=title),
    }


def _outcomes(envelope: dict, front_end: str) -> list[str]:
    """Per-item outcome, normalized to `new` / `existing` / `failed`.

    The three envelopes disagree on spelling; the property under test is the
    same in all three, so it is asserted on a common vocabulary rather than on
    whichever word each front end happens to use.
    """
    out: list[str] = []
    for item in envelope["items"]:
        if front_end == "inbox":
            out.append({"added": "new", "exists": "existing"}.get(
                item["status"], "failed"))
            continue
        action = (
            item.get("result", {}).get("action") if front_end == "add"
            else item.get("action")
        )
        out.append({"insert": "new", "update": "existing"}.get(action, "failed"))
    return out


# ---------------------------------------------------------------------------
# C3 — a batch that captured nothing reports status "error"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("front_end", ["add", "inbox"])
def test_a_batch_that_captured_nothing_reports_error_status(
    front_end: str, tmp_path: Path
) -> None:
    """C3, through the CLI, for the two front ends that can reach it there.

    `import`'s all-failed case is not reachable from BibTeX input — a source
    that fails to parse returns `error` from an earlier guard, and one that
    parses imports successfully — so it is covered at service level below.
    """
    with stub_translation_server({}) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        batch = tmp_path / "batch.txt"
        batch.write_text("not-resolvable-anywhere\nalso-not-resolvable\n")
        argv = (
            ["add", "--from-file", str(batch)] if front_end == "add"
            else ["inbox", str(batch)]
        )
        proc = _run_cli(
            [*argv, "--json", "--delay", "0", "--config", str(config_path)],
            home=tmp_path,
        )

    assert proc.returncode != exit_codes.OK, (
        f"{front_end}: expected a non-zero exit\n{proc.stdout}\n{proc.stderr}"
    )
    envelope = json.loads(proc.stdout)
    assert envelope["status"] == "error", (
        f"{front_end}: exited {proc.returncode} but reported "
        f"status={envelope['status']!r} — the two channels disagree"
    )
    assert envelope["errors"], f"{front_end}: no reasons in the errors channel"


def test_import_reports_error_status_when_every_record_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """C3 for `import`, at the level where an all-failed batch is reachable.

    Every record erroring needs a failure inside the batch writer, which no
    BibTeX input produces on its own — so the writer is stubbed and the
    question asked of `import_from_bibtex` alone: does it still say `ok` when
    it imported nothing?
    """
    from pzi import import_service

    def _all_records_fail(*, bib, records, **kwargs):
        return [
            {"status": "error", "message": "failed to import record",
             "errors": ["stubbed failure"], "citekey": None}
            for _ in records
        ]

    monkeypatch.setattr(
        import_service, "add_records_to_bib_batch", _all_records_fail
    )
    config_path = _write_config(tmp_path)
    source = tmp_path / "source.bib"
    source.write_text(
        "@article{alpha,\n title={Alpha},\n year={2021},\n doi={10.1000/a}\n}\n\n"
        "@article{beta,\n title={Beta},\n year={2022},\n doi={10.1000/b}\n}\n"
    )

    result = import_service.import_from_bibtex(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        source_path=str(source),
        bib_selector=None,
        dry_run=False,
    )

    assert result["errors"], "every record failed but nothing reached errors[]"
    assert result["status"] == "error", (
        f"imported nothing and reported status={result['status']!r}"
    )


@pytest.mark.parametrize("front_end", ["add", "inbox"])
def test_a_batch_that_captured_something_reports_ok_status(
    front_end: str, tmp_path: Path
) -> None:
    """The other half of C3: partial success is not an error status.

    Without this, "derive the status" could be satisfied by reporting `error`
    whenever anything failed — the behaviour `add.py`'s envelope fix
    deliberately removed, since the exit code already distinguishes PARTIAL.
    """
    with stub_translation_server(_stub_paper()) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        batch = tmp_path / "batch.txt"
        batch.write_text(f"{_DOI}\nnot-resolvable-anywhere\n")
        argv = (
            ["add", "--from-file", str(batch)] if front_end == "add"
            else ["inbox", str(batch)]
        )
        proc = _run_cli(
            [*argv, "--json", "--delay", "0", "--config", str(config_path)],
            home=tmp_path,
        )

    envelope = json.loads(proc.stdout)
    assert envelope["counts"]["added"] == 1, proc.stdout
    assert envelope["counts"]["failed"] == 1, proc.stdout
    assert envelope["status"] == "ok", (
        f"{front_end}: a batch that captured something is not an error"
    )


@pytest.mark.parametrize("succeeded", [0, 1, 7])
@pytest.mark.parametrize("failed", [0, 1, 7])
def test_status_and_exit_code_never_disagree(succeeded: int, failed: int) -> None:
    """The rule under C3, stated directly on the pair that has to agree.

    `batch_status` and `batch_exit_code` take the same two counts and answer on
    two channels. C3 was those channels disagreeing, so the property is asserted
    on the helpers themselves and not only through the front ends: a non-zero
    exit must never travel with `status: "ok"`, and OK must never travel with
    `status: "error"`.
    """
    from pzi.add_planning import batch_status
    from pzi.commands.common import batch_exit_code

    status = batch_status(succeeded=succeeded, failed=failed)
    code = batch_exit_code(succeeded=succeeded, failed=failed)

    if code == exit_codes.OK:
        assert status == "ok"
    elif code == exit_codes.ENVIRONMENT:
        assert status == "error", "nothing succeeded, so both channels say failed"
    else:
        assert status == "ok", "PARTIAL means something succeeded"


# ---------------------------------------------------------------------------
# C4 — a dry-run preview predicts the actions the real run takes
# ---------------------------------------------------------------------------


def _c4_inputs(front_end: str, tmp_path: Path) -> tuple[list[str], Path]:
    """Two inputs naming one paper, expressed the way each front end takes it."""
    if front_end == "import":
        source = tmp_path / "source.bib"
        source.write_text(
            "@article{alpha,\n title={Alpha Paper},\n author={Brown, B},\n"
            " year={2021},\n doi={10.1000/alpha}\n}\n\n"
            "@article{alphadup,\n title={Alpha Paper Revised},\n"
            " author={Brown, B},\n year={2021},\n doi={10.1000/alpha}\n}\n"
        )
        return ["import", str(source)], source
    batch = tmp_path / "batch.txt"
    batch.write_text(f"{_DOI}\n{_DOI_URL}\n")
    argv = (
        ["add", "--from-file", str(batch)] if front_end == "add"
        else ["inbox", str(batch)]
    )
    return argv, batch


@pytest.mark.parametrize("front_end", ["add", "inbox", "import"])
def test_dry_run_preview_sees_earlier_items_in_its_own_batch(
    front_end: str, tmp_path: Path
) -> None:
    """C4. Two inputs resolving to one paper: preview must match the real run.

    The real run is already correct on every front end — each item is visible
    to the next, through the session in `import` and through the written file
    in the other two. Only the preview was wrong, and only where there is no
    session: dry-run writes nothing, so the single-capture path re-read a
    library that never changed. `import` is the passing control here.
    """
    with stub_translation_server(_stub_paper()) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        argv, _ = _c4_inputs(front_end, tmp_path)
        common = ["--json", "--delay", "0", "--config", str(config_path)]
        if front_end == "import":
            common.remove("--delay")
            common.remove("0")

        preview = _run_cli([*argv, *common, "--dry-run"], home=tmp_path)
        predicted = _outcomes(json.loads(preview.stdout), front_end)

        # The preview wrote nothing, so the real run starts from the same state.
        assert (tmp_path / "ml.bib").read_text() == "", "dry-run wrote to the library"

        real = _run_cli([*argv, *common], home=tmp_path)
        actual = _outcomes(json.loads(real.stdout), front_end)

    assert predicted == actual, (
        f"{front_end}: preview said {predicted}, run did {actual}"
    )
    assert actual == ["new", "existing"], (
        f"{front_end}: two inputs for one paper must add once, got {actual}"
    )


@pytest.mark.parametrize("front_end", ["add", "inbox"])
def test_dry_run_preview_leaves_distinct_items_alone(
    front_end: str, tmp_path: Path
) -> None:
    """The accumulator must match on identity, not on position.

    The cheap wrong fix — treat everything after the first item as seen —
    passes the test above and fails this one.
    """
    items = {**_stub_paper(), _OTHER_DOI: translation_item(title="Another Paper")}
    with stub_translation_server(items) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        batch = tmp_path / "batch.txt"
        batch.write_text(f"{_DOI}\n{_OTHER_DOI}\n{_DOI_URL}\n")
        argv = (
            ["add", "--from-file", str(batch)] if front_end == "add"
            else ["inbox", str(batch)]
        )
        proc = _run_cli(
            [*argv, "--json", "--dry-run", "--delay", "0",
             "--config", str(config_path)],
            home=tmp_path,
        )

    predicted = _outcomes(json.loads(proc.stdout), front_end)
    assert predicted == ["new", "new", "existing"], predicted


def test_batch_only_update_says_why_it_has_no_diff(tmp_path: Path) -> None:
    """The one thing the accumulator cannot preview, reported rather than faked.

    An item updating an entry an earlier item in the same batch would insert has
    nothing on disk to diff against. Before, that item previewed as a second
    insert with a full diff — confidently wrong. It must now carry the right
    action and an empty diff that says why, not a wrong diff and not an error.
    """
    with stub_translation_server(_stub_paper()) as server_url:
        config_path = _write_config(
            tmp_path, extra=f'translation_server_url = "{server_url}"\n'
        )
        batch = tmp_path / "batch.txt"
        batch.write_text(f"{_DOI}\n{_DOI_URL}\n")
        proc = _run_cli(
            ["add", "--from-file", str(batch), "--json", "--dry-run",
             "--delay", "0", "--config", str(config_path)],
            home=tmp_path,
        )

    first, second = json.loads(proc.stdout)["items"]
    assert first["result"]["diff"], "the first item still previews a real diff"
    assert second["status"] != "error", "a batch-only update is not a failure"
    assert second["result"]["action"] == "update"
    assert second["result"]["diff"] == ""
    assert any(
        "earlier item in this batch" in w
        for w in second["result"].get("warnings", [])
    ), second["result"].get("warnings")
