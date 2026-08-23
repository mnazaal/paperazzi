import base64
import json
import os
import subprocess
from unittest.mock import patch

import pytest

from pzi.browser_pdf import (
    _HOOK_OVERHEAD_SECONDS,
    _validate_browser_command,
    discover_pdf_url_with_browser,
    download_pdf_with_browser,
)


def test_validate_browser_command_expands_tilde_tokens() -> None:
    tokens = _validate_browser_command(
        "~/.local/bin/python -m pzi.browser_pdf_hook --profile ~/.mozilla/p"
    )
    home = os.path.expanduser("~")
    assert tokens[0] == f"{home}/.local/bin/python"
    assert tokens[-1] == f"{home}/.mozilla/p"
    # Non-path tokens are untouched.
    assert "--profile" in tokens


def test_validate_browser_command_leaves_plain_tokens_unchanged() -> None:
    assert _validate_browser_command("python -m pzi.browser_pdf_hook") == [
        "python",
        "-m",
        "pzi.browser_pdf_hook",
    ]


def _mock_subprocess(stdout: str = "", returncode: int = 0):
    """Helper to create a mock subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["mock-cmd"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


# --- discover_pdf_url_with_browser tests ---


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_json_output(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"pdf_url": "https://example.com/paper.pdf"})
    )
    result = discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
    )
    assert result == "https://example.com/paper.pdf"


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_plain_url(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout="https://example.com/paper.pdf"
    )
    result = discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
    )
    assert result == "https://example.com/paper.pdf"


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_nonzero_returncode(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(returncode=1)
    result = discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_empty_stdout(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(stdout="   ")
    result = discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_non_url_plain(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(stdout="not a url")
    result = discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_passes_doi(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"pdf_url": "https://example.com/paper.pdf"})
    )
    discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
        doi="10.1234/foo",
    )
    sent_input = mock_run.call_args[1]["input"]
    payload = json.loads(sent_input)
    assert payload["doi"] == "10.1234/foo"
    assert payload["page_url"] == "https://journal.org/article"


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_json_no_pdf_url_key(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"status": "ok"})
    )
    result = discover_pdf_url_with_browser(
        command="mock-cmd",
        page_url="https://journal.org/article",
    )
    assert result is None


# --- download_pdf_with_browser tests ---


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_success(mock_run) -> None:
    pdf_content = b"%PDF-1.4\ntest pdf content"
    pdf_base64 = base64.b64encode(pdf_content).decode("ascii")
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"pdf_base64": pdf_base64})
    )
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result == pdf_content


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_nonzero_returncode(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(returncode=1)
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_empty_stdout(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(stdout="")
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_invalid_base64(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"pdf_base64": "not-valid-base64!!!"})
    )
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_not_pdf_content(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"pdf_base64": base64.b64encode(b"NOT A PDF").decode()})
    )
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_missing_pdf_base64_key(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps({"status": "error"})
    )
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_non_dict_json(mock_run) -> None:
    mock_run.return_value = _mock_subprocess(
        stdout=json.dumps(["not", "a", "dict"])
    )
    result = download_pdf_with_browser(
        command="mock-cmd",
        pdf_url="https://example.com/paper.pdf",
    )
    assert result is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_returns_none_when_the_binary_is_missing(
    mock_run,
) -> None:
    """A misconfigured hook must report "no PDF", not raise.

    `fetch_and_store_pdf_with_fallbacks` advances on a falsy return and has no
    exception handling, so raising here aborted the whole chain and skipped the
    FlareSolverr and desktop fallbacks.
    """
    mock_run.side_effect = FileNotFoundError(2, "No such file or directory")

    assert download_pdf_with_browser(command="/nope/browser", pdf_url="https://x/p.pdf") is None


@patch("pzi.browser_pdf.subprocess.run")
def test_download_pdf_with_browser_returns_none_on_permission_error(mock_run) -> None:
    mock_run.side_effect = PermissionError(13, "Permission denied")

    assert download_pdf_with_browser(command="/bin/hook", pdf_url="https://x/p.pdf") is None


def test_download_pdf_with_browser_returns_none_on_unbalanced_quote() -> None:
    """`shlex.split` raises ValueError on an unterminated quote in config."""
    assert download_pdf_with_browser(
        command='python hook.py --profile "unterminated', pdf_url="https://x/p.pdf"
    ) is None


def test_download_pdf_with_browser_returns_none_on_empty_command() -> None:
    assert download_pdf_with_browser(command="   ", pdf_url="https://x/p.pdf") is None


@patch("pzi.browser_pdf.subprocess.run")
def test_discover_pdf_url_with_browser_returns_none_when_the_binary_is_missing(
    mock_run,
) -> None:
    mock_run.side_effect = FileNotFoundError(2, "No such file or directory")

    assert discover_pdf_url_with_browser(command="/nope/browser", page_url="https://x") is None


def test_discover_pdf_url_with_browser_returns_none_on_unbalanced_quote() -> None:
    """The sibling leaked ValueError too, despite catching OSError."""
    assert discover_pdf_url_with_browser(
        command='python hook.py --profile "unterminated', page_url="https://x"
    ) is None


@pytest.mark.parametrize(
    ("invoke", "kwargs"),
    [
        (discover_pdf_url_with_browser, {"page_url": "https://journal.org/article"}),
        (download_pdf_with_browser, {"pdf_url": "https://journal.org/paper.pdf"}),
    ],
    ids=["discover", "download"],
)
@patch("pzi.browser_pdf.subprocess.run")
def test_every_hook_invocation_budgets_for_the_challenge_wait(
    mock_run, invoke, kwargs: dict[str, str]
) -> None:
    """Both hook entry points derive their timeout from the child's own arguments.

    `_hook_timeout_seconds` exists so the parent's budget and the child's
    `--challenge-timeout` cannot drift; the discovery entry point hardcoded 120,
    which is *below* the 240 s wait it was asking the child to perform, so a
    headful CAPTCHA solve was killed at the moment it became useful.
    """
    mock_run.return_value = _mock_subprocess(stdout="{}")

    invoke(
        command="mock-cmd --headful --challenge-timeout 240",
        **kwargs,
    )

    assert mock_run.call_args.kwargs["timeout"] == 240 + _HOOK_OVERHEAD_SECONDS
