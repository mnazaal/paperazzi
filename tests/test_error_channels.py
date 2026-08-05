"""Failures have to reach the caller, in the channel the caller reads.

Every case here ran, failed, and reported success — or reported failure with the
reason removed. The shared envelope merge exists because hand-building that
document per command is what dropped the keys in the first place.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from pzi import cli_json, exit_codes

# ---------------------------------------------------------------------------
# One envelope, built once
# ---------------------------------------------------------------------------


def test_merging_targets_keeps_every_list_key_a_service_reported() -> None:
    merged = cli_json.merge_target_results(
        [
            ("ml", {"status": "ok", "bib_name": "ml", "matches": [{"citekey": "a1"}],
                    "warnings": ["duplicate citekey 'x'"], "errors": []}),
            ("cs", {"status": "ok", "bib_name": "cs", "matches": [{"citekey": "b1"}],
                    "warnings": [], "errors": []}),
        ],
        command="search",
    )

    assert merged["status"] == "ok"
    assert [item["citekey"] for item in merged["items"]] == ["a1", "b1"]
    # The partial-parse warning text mode prints is no longer lost in --json.
    assert merged["warnings"] == ["ml: duplicate citekey 'x'"]
    assert merged["searched_bibs"] == ["ml", "cs"]


def test_merging_targets_names_the_one_that_failed() -> None:
    merged = cli_json.merge_target_results(
        [
            ("ml", {"status": "ok", "bib_name": "ml", "matches": [], "errors": []}),
            ("cs", {"status": "error", "bib_name": None, "matches": [],
                    "errors": ["missing bib"]}),
        ],
        command="search",
    )

    assert merged["status"] == "error"
    assert merged["errors"] == ["cs: missing bib"]


def test_merging_a_single_target_does_not_prefix_its_errors() -> None:
    merged = cli_json.merge_target_results(
        [("ml", {"status": "error", "bib_name": "ml", "items": [],
                 "errors": ["missing bib"]})],
        command="update",
    )

    assert merged["errors"] == ["missing bib"]


def test_promote_summary_survives_the_json_envelope() -> None:
    """`summary` is where promotion's `provider_errors` live; the hand-built
    envelope never copied it."""
    merged = cli_json.merge_target_results(
        [("ml", {"status": "ok", "bib_name": "ml", "items": [],
                 "summary": {"provider_errors": 3}, "errors": []})],
        command="update --promote",
    )

    assert merged["summary"] == {"provider_errors": 3}


# ---------------------------------------------------------------------------
# Status derived from item outcomes
# ---------------------------------------------------------------------------


def test_an_update_where_every_item_failed_is_not_ok(tmp_path: Path) -> None:
    """`POST /update` returns this verbatim, so it answered 200
    `{"status":"ok","errors":[]}` for a run in which nothing worked."""
    from pzi.add_service import add_record_to_bib
    from pzi.update_service import update_bib

    bib_path = tmp_path / "ml.bib"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    add_record_to_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        record={"citekey": "a2024", "title": "A", "arxiv_id": "2401.00001"},
        bib_selector=None,
        dry_run=False,
    )

    def _failing_search(query: str, *, server_url: str):
        raise OSError("connection refused")

    result = update_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        dry_run=False,
        fetch_search=_failing_search,
    )

    assert result["status"] == "error"
    assert result["errors"]


def test_a_failed_quarantine_move_is_not_rendered_as_a_dry_run(tmp_path: Path) -> None:
    from pzi.cli_render import _render_clean_result

    lines = _render_clean_result(
        {
            "status": "error",
            "bib_path": str(tmp_path / "main.bib"),
            "papers_dir": str(tmp_path / "papers"),
            "total_entries": 1,
            "issues": [],
            "actions": [
                {
                    "type": "move_orphan",
                    "source": "a.pdf",
                    "destination": ".orphans/a.pdf",
                    "done": False,
                    "error": "Permission denied",
                }
            ],
            "errors": ["could not move a.pdf to .orphans/a.pdf: Permission denied"],
        },
        dry_run=False,
    )

    text = "\n".join(lines)
    assert "would do" not in text
    assert "failed: move_orphan: Permission denied" in text


# ---------------------------------------------------------------------------
# Reasons survive the trip
# ---------------------------------------------------------------------------


def test_from_file_json_reports_why_an_item_failed(tmp_path: Path) -> None:
    """`first_error` was handed the whole result Mapping, which always returns
    None, so this documented channel was the literal string "failed"."""
    from pzi.commands.add import _failure_reason

    assert _failure_reason({"message": "invalid input", "errors": []}) == "invalid input"
    assert _failure_reason({"errors": ["connection refused"]}) == "connection refused"
    assert _failure_reason({}) == "failed"


def test_every_pdf_stage_contributes_a_failure_reason(monkeypatch) -> None:
    """Only the direct stage did, so a broken browser_pdf_cmd or FlareSolverr
    failure was stderr-only — under --json the operator could not tell which
    stage broke, or whether it ran."""
    from pzi import pdf as pdf_module

    monkeypatch.setattr(
        pdf_module,
        "fetch_and_store_pdf",
        lambda **_kwargs: (None, "HTTP 403"),
    )
    monkeypatch.setattr(
        "pzi.browser_pdf.download_pdf_with_browser", lambda **_kwargs: b"<html>"
    )
    monkeypatch.setattr(
        "pzi.flaresolverr.fetch_pdf_via_flaresolverr", lambda *_a, **_k: None
    )

    path, warning, error = pdf_module.fetch_and_store_pdf_with_fallbacks(
        url="https://example.com/paper.pdf",
        papers_dir="/tmp/pzi-does-not-exist",
        citekey="k1",
        browser_pdf_cmd="fake-cmd",
        flaresolverr_url="http://127.0.0.1:8191",
    )

    assert path is None and warning is None
    assert "direct download: HTTP 403" in error
    assert "browser_pdf_cmd: response was not a PDF" in error
    assert "FlareSolverr: no PDF returned" in error


def test_semantic_scholar_reports_a_rate_limit_instead_of_no_result() -> None:
    """S2 answers quota/rate-limit/auth failures with HTTP 200 and an `error`
    key, which read as "no such paper"."""
    from pzi.metadata_sources import fetch_semantic_scholar_record

    errors: list[str] = []
    record = fetch_semantic_scholar_record(
        "10.1/x",
        fetch_text=lambda url, **_kwargs: '{"error": "Too Many Requests"}',
        errors=errors,
    )

    assert record is None
    assert errors == ["semantic-scholar: Too Many Requests"]


# ---------------------------------------------------------------------------
# A refusal to write is an error message, not a stack trace
# ---------------------------------------------------------------------------

_MALFORMED_LIBRARY = """@article{smith2019graph,
  title = {Graph Networks\\},
  year = {2019},
}

@article{jones2020deep,
  title = {Deep},
  year = {2020},
}
"""


def _library_that_refuses_writes(tmp_path: Path) -> Path:
    """A bib with one unparseable block — every write to it must be refused."""
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(_MALFORMED_LIBRARY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )
    return config_path


def _run(argv: list[str], tmp_path: Path) -> tuple[int, str, str]:
    from pzi.cli import run_cli

    stdout, stderr = io.StringIO(), io.StringIO()
    code = run_cli(argv, home_dir=str(tmp_path), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_refusing_to_rewrite_a_malformed_bib_is_an_error_not_a_traceback(
    tmp_path: Path,
) -> None:
    config_path = _library_that_refuses_writes(tmp_path)

    code, stdout, stderr = _run(
        ["tag", "add", "jones2020deep", "ok", "--config", str(config_path)], tmp_path
    )

    assert code == exit_codes.ENVIRONMENT
    assert "malformed BibTeX" in stderr
    assert "Traceback" not in stderr
    assert stdout == ""


def test_refusing_to_rewrite_a_malformed_bib_still_answers_in_json(
    tmp_path: Path,
) -> None:
    """`--json` promises a parseable document on every outcome, failures included."""
    import json

    config_path = _library_that_refuses_writes(tmp_path)

    code, stdout, _stderr = _run(
        ["tag", "add", "jones2020deep", "ok", "--json", "--config", str(config_path)],
        tmp_path,
    )

    assert code == exit_codes.ENVIRONMENT
    envelope = json.loads(stdout)
    assert envelope["status"] == "error"
    assert any("malformed BibTeX" in message for message in envelope["errors"])


# ---------------------------------------------------------------------------
# The extension's error channel
# ---------------------------------------------------------------------------


def test_the_extension_reads_the_servers_singular_error_key() -> None:
    import subprocess

    script = """
import { responseErrors } from "./browser-extension/background/utils.js";
const fromError = responseErrors({ error: "invalid API token" }, "failed");
const fromErrors = responseErrors({ errors: ["a", "b"] }, "failed");
const fallback = responseErrors({}, "failed");
console.log(JSON.stringify([fromError, fromErrors, fallback]));
"""
    node = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    # Asserted, not skipped. Node is required to run the extension tests at all
    # (`test_browser_extension_js` skips only when it is absent from PATH), so a
    # *failing* node here means the module under test is broken — and reporting
    # that as a skip is how it would stay broken.
    assert node.returncode == 0, f"node failed: {node.stderr.strip()[:500]}"

    import json

    from_error, from_errors, fallback = json.loads(node.stdout)
    assert from_error == ["invalid API token"]
    assert from_errors == ["a", "b"]
    assert fallback == ["failed"]


# ---------------------------------------------------------------------------
# Search and dedupe reporting
# ---------------------------------------------------------------------------


def test_a_tag_that_normalizes_to_nothing_is_refused_not_ignored(tmp_path: Path) -> None:
    """It fell through as "no tag filter" and returned every entry."""
    from pzi.search_service import search_bib

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        "@article{a1,\n  title = {A},\n  keywords = {ml},\n}\n", encoding="utf-8"
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )

    result = search_bib(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        query=None,
        author=None,
        year=None,
        tag="!!",
    )

    assert result["status"] == "error"
    assert result["matches"] == []


def test_duplicate_clusters_are_connected_components() -> None:
    """One cluster per index bucket repeated a pair once for its DOI and again
    for its arXiv id, and split a transitive three-way duplicate in two."""
    from pzi.dedupe_service import _identity_components

    components = _identity_components(
        {
            ("doi", "10.1/x"): [0, 1],
            ("arxiv", "2401.1"): [0, 1],
            ("url", "https://example.com/p"): [1, 2],
            ("doi", "10.1/other"): [3],
        }
    )

    assert [sorted(component) for component in components] == [[0, 1, 2]]


def test_csv_export_neutralizes_formula_cells(tmp_path: Path) -> None:
    from pzi.export_service import export_csv

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(
        '@article{a1,\n  title = {=HYPERLINK("http://evil","click")},\n}\n',
        encoding="utf-8",
    )

    content = export_csv(bib_path=str(bib_path))["content"]

    assert "'=HYPERLINK" in content


def test_exit_code_partial_is_reachable_for_promote() -> None:
    """`PromoteItem` had no `failed` key, so the runner's PARTIAL branch could
    never be taken and a run where every promotion failed exited 0."""
    from pzi.commands.update import run_update_command

    class _Args:
        promote = True
        replace = False
        mark_resolved = False
        dry_run = False
        json = False
        verbose = False
        target = None

    def _promote(**_kwargs):
        return {
            "status": "ok",
            "bib_name": "ml",
            "dry_run": False,
            "keep_preprint": True,
            "items": [
                {
                    "preprint_citekey": "a1",
                    "published_citekey": None,
                    "action": "skip",
                    "changed_fields": [],
                    "pdf_attached": None,
                    "note": "promotion failed: boom",
                    "failed": True,
                }
            ],
            "errors": ["a1: promotion failed: boom"],
        }

    code = run_update_command(
        _Args(),
        home_dir="/tmp",
        config_path="/tmp/c.toml",
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        promote_bib_fn=_promote,
    )

    assert code == exit_codes.PARTIAL


# ---------------------------------------------------------------------------
# The failure channel is never empty
# ---------------------------------------------------------------------------


def test_an_error_envelope_without_errors_falls_back_to_its_message() -> None:
    """`errors[]` is the documented failure channel and must say something.

    `fix merge` reported every failure as `status: error` plus a `message` and
    no `errors` at all, so a consumer branching on the documented channel saw a
    failed command with nothing wrong. Any service that forgets is covered here
    rather than one command at a time.
    """
    envelope = cli_json.build_envelope(
        {"status": "error", "message": "entry not found: smith2020", "errors": []},
        command="fix merge",
    )

    assert envelope["errors"] == ["entry not found: smith2020"]


def test_an_error_envelope_with_no_message_at_all_still_says_something() -> None:
    envelope = cli_json.build_envelope({"status": "error"}, command="fix merge")

    assert envelope["errors"] and envelope["errors"][0]


def test_a_successful_envelope_keeps_its_empty_error_list() -> None:
    envelope = cli_json.build_envelope(
        {"status": "ok", "message": "merged a into b", "errors": []},
        command="fix merge",
    )

    assert envelope["errors"] == []


def test_errors_a_service_did_report_are_left_alone() -> None:
    envelope = cli_json.build_envelope(
        {"status": "error", "message": "summary", "errors": ["the real reason"]},
        command="fix merge",
    )

    assert envelope["errors"] == ["the real reason"]


def test_add_json_emits_an_envelope_when_the_backend_is_not_ready(tmp_path: Path) -> None:
    """`--json` promises exactly one parseable document on every outcome.

    The commonest failure of all — the translation server not running — printed
    prose to stderr and nothing at all to stdout, so a script driving `pzi add
    --json` got an empty document and had to scrape stderr to find out why.
    """
    import json
    from argparse import Namespace

    from pzi.commands.add import run_add_command

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n', encoding="utf-8"
    )

    args = Namespace(
        value="10.1145/3372297", tags=None, bib=None, dry_run=False, json=True,
        verbose=False, citekey=None, force_new=False, from_file=None, delay=None,
        failures_out=None, metadata_json=None, cookie_file=None, pdf_candidate=None,
        page_html=None, strict_metadata=False, browser=None, target=None,
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    code = run_add_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(config_path),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
        backend_session_fn=lambda *_a, **_k: _NotReadyBackend(),
    )

    assert code == exit_codes.ENVIRONMENT
    envelope = json.loads(stdout.getvalue())
    assert envelope["status"] == "error"
    assert any("translation server" in e.lower() for e in envelope["errors"])


class _NotReadyBackend:
    def __enter__(self):
        return {"ready": False}

    def __exit__(self, *_exc):
        return False


def test_a_violated_internal_invariant_still_emits_a_json_document(tmp_path: Path) -> None:
    """`RuntimeError` escaped the CLI boundary entirely.

    `_invariant` and `BatchWriteSession.apply_plan` raise a bare `RuntimeError`
    on a violated batch-state guard, and `run_cli` caught `PziError`, `OSError`,
    `UnicodeDecodeError` and `ValueError` but not that — so a desync printed a
    traceback and, under `--json`, wrote nothing at all to stdout. Those are the
    two outcomes `--json` exists to rule out.
    """
    from pzi import cli

    out, err = io.StringIO(), io.StringIO()
    with patch(
        "pzi.commands.entries.list_entries",
        side_effect=RuntimeError("internal invariant violated: snapshots disagree"),
    ):
        code = cli.run_cli(
            ["entries", "--json"], home_dir=str(tmp_path), stdout=out, stderr=err
        )

    assert code == exit_codes.ENVIRONMENT
    document = json.loads(out.getvalue())
    assert document["status"] == "error"
    assert any("invariant" in e for e in document["errors"]), document
    assert "Traceback" not in err.getvalue()
