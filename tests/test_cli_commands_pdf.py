from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi.commands.pdf import run_pdf_command


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
    assert stderr.getvalue() == "error: citekey required (or use --failed-only for batch retry)\n"


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

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
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
