import base64
import json
import subprocess
from unittest.mock import patch

from pzi.browser_pdf import discover_pdf_url_with_browser, download_pdf_with_browser


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
