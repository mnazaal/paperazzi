from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi import exit_codes
from pzi.commands.pdf import run_pdf_command
from pzi.errors import REASON_USAGE


def test_run_pdf_command_attach_uses_injected_service(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_attach_pdf(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": "ml",
            "citekey": kwargs["citekey"],
            "local_pdf_path": str(tmp_path / "paper.pdf"),
            "warnings": [],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(
        pdf_command="attach",
        citekey="smith2024graph",
        source="https://example.com/paper.pdf",
    )

    exit_code = run_pdf_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector="ml",
        attach_pdf_fn=fake_attach_pdf,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
            "citekey": "smith2024graph",
            "source": "https://example.com/paper.pdf",
        }
    ]
    assert "attached" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_run_pdf_command_retry_requires_citekey(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(pdf_command="retry", citekey=None, failed_only=False)

    exit_code = run_pdf_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "pzi pdf retry: error: citekey required (or use --failed-only for batch retry)\n"
        "Run 'pzi pdf retry --help' for usage.\n"
    )


def test_run_pdf_command_failed_only_uses_injected_service(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_retry_failed_pdfs(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": "ml",
            "succeeded": 1,
            "total": 2,
            "skipped_already_has_pdf": 3,
            "skipped_no_url": 4,
            "failures": [{"citekey": "bad2024", "error": "no pdf"}],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(pdf_command="retry", citekey=None, failed_only=True)

    exit_code = run_pdf_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector="ml",
        retry_failed_pdfs_fn=fake_retry_failed_pdfs,
    )

    # A run that reports failures exits PARTIAL in text mode too — the exit code
    # must not depend on whether --json was passed.
    assert exit_code == exit_codes.PARTIAL
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
            # `--discover` is off by default: the batch derives URLs from
            # identifiers the entries already carry, without network calls.
            "deep": False,
        }
    ]
    assert stdout.getvalue().splitlines() == [
        "bib: ml",
        "succeeded: 1/2",
        "skipped (already have PDF): 3",
        "skipped (no PDF URL): 4",
        "failed: 1",
        "  bad2024: no pdf",
    ]
    assert stderr.getvalue() == ""


def test_run_pdf_command_failed_only_all_failed_is_environment(tmp_path: Path) -> None:
    """A batch in which *nothing* succeeded is 5, not 4.

    That is the shared rule in `batch_exit_code`, which every other batch
    command routes through. This one open-coded `PARTIAL if failures else OK`
    on both output paths, so an all-failed retry claimed a partial success that
    did not happen. `exit_codes.py` and the `--help` epilog omitted `pdf retry`
    from the batch list as well, which is why the exit-code table test passed
    over it — docs and code wrong together is the one disagreement a
    docs-vs-code test cannot see.
    """
    def fake_retry_failed_pdfs(**_kwargs):
        return {
            "status": "ok",
            "bib_name": "ml",
            "succeeded": 0,
            "total": 1,
            "skipped_already_has_pdf": 0,
            "skipped_no_url": 0,
            "failures": [{"citekey": "bad2024", "error": "connection refused"}],
        }

    for as_json in (False, True):
        exit_code = run_pdf_command(
            Namespace(pdf_command="retry", citekey=None, failed_only=True, json=as_json),
            home_dir=str(tmp_path),
            config_path=str(tmp_path / "config.toml"),
            stdout=StringIO(),
            stderr=StringIO(),
            bib_selector="ml",
            retry_failed_pdfs_fn=fake_retry_failed_pdfs,
        )
        assert exit_code == exit_codes.ENVIRONMENT, f"json={as_json}"


def test_run_pdf_command_failed_only_error_maps_the_reason_the_result_carries(
    tmp_path: Path,
) -> None:
    """A whole-batch failure exits by its own `reason`, not a hardcoded ENVIRONMENT.

    Both `--failed-only` output branches open-coded `exit_codes.ENVIRONMENT` on
    `status == "error"`, unlike the sibling single-`retry`/`attach` branches a
    few lines below, which already route through `_pdf_exit_code`. `REASON_USAGE`
    is used here to make the fork visible, since the one reason `pdf_service`
    reaches on this path today (`REASON_CONFIG`) happens to also map to
    `ENVIRONMENT`.
    """

    def fake_retry_failed_pdfs(**_kwargs):
        return {"status": "error", "message": "bad selector", "errors": [], "reason": REASON_USAGE}

    for as_json in (False, True):
        exit_code = run_pdf_command(
            Namespace(pdf_command="retry", citekey=None, failed_only=True, json=as_json),
            home_dir=str(tmp_path),
            config_path=str(tmp_path / "config.toml"),
            stdout=StringIO(),
            stderr=StringIO(),
            bib_selector="ml",
            retry_failed_pdfs_fn=fake_retry_failed_pdfs,
        )
        assert exit_code == exit_codes.USAGE, f"json={as_json}"


def test_pdf_retry_unknown_citekey_is_not_found(tmp_path: Path) -> None:
    """`pdf_service` never set `reason`, so the runner's not-found branch was dead.

    `_pdf_exit_code` was already correct; the result it read simply carried no
    `reason`, so every unknown citekey fell through to ENVIRONMENT.
    """
    from pzi.pdf_service import retry_pdf

    bib_path = tmp_path / "ml.bib"
    bib_path.write_text("@article{real2024, title = {Real}}\n")
    papers = tmp_path / "papers"
    papers.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\n'
        f'papers_dir = "{papers}"\ndefault = true\n'
    )

    result = retry_pdf(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        bib_selector=None,
        citekey="nosuch2024",
    )

    assert result["status"] == "error"
    assert result["reason"] == "not_found"


def test_run_pdf_command_rejects_failed_only_combined_with_a_citekey(tmp_path: Path) -> None:
    """The citekey used to be accepted and silently discarded.

    That was documented in the flag's help and in the README, but doing
    something other than what was typed is worse than refusing:
    `pzi pdf retry smith2024 --failed-only` reads as "retry this one entry" and
    retried the entire library.
    """
    called: list[dict] = []
    stdout, stderr = StringIO(), StringIO()

    exit_code = run_pdf_command(
        Namespace(
            pdf_command="retry",
            citekey="smith2024",
            failed_only=True,
            json=False,
            target=None,
        ),
        config_path=str(tmp_path / "config.toml"),
        home_dir=str(tmp_path),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
        retry_failed_pdfs_fn=lambda **kw: called.append(kw) or {},
    )

    assert exit_code == 2
    assert called == [], "the batch retry ran despite the usage error"
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "pzi pdf retry: error: --failed-only retries every PDF-less entry; "
        "drop the citekey, or drop --failed-only to retry just that entry\n"
        "Run 'pzi pdf retry --help' for usage.\n"
    )
