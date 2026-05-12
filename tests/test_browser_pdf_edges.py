"""Edge tests for browser_pdf.py uncovered lines (65-66: download_pdf_with_browser errors)."""

import base64
import json
import subprocess

from pzi.browser_pdf import discover_pdf_url_with_browser, download_pdf_with_browser

# ── download_pdf_with_browser ────────────────────────────────────

def test_download_pdf_nonzero_rc(monkeypatch) -> None:
    """Return code != 0 → None."""
    class FakeResult:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_empty_stdout(monkeypatch) -> None:
    """Empty stdout → None."""
    class FakeResult:
        returncode = 0
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_invalid_json(monkeypatch) -> None:
    """JSON decode error → None."""
    class FakeResult:
        returncode = 0
        stdout = "not json"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_result_not_dict(monkeypatch) -> None:
    """Result is not a dict → None."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps(["list not dict"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_missing_pdf_base64(monkeypatch) -> None:
    """Dict but missing pdf_base64 key → None."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"other": 1})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_base64_not_string(monkeypatch) -> None:
    """pdf_base64 exists but is not a string → None."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"pdf_base64": 123})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_invalid_base64(monkeypatch) -> None:
    """Invalid base64 decode → None (line ~65)."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"pdf_base64": "!!!not-valid-base64!!!"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_non_pdf_bytes(monkeypatch) -> None:
    """Base64 decodes but bytes don't start with %PDF- → None (line ~66)."""
    non_pdf = base64.b64encode(b"not a pdf file").decode()
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"pdf_base64": non_pdf})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf") is None


def test_download_pdf_success(monkeypatch) -> None:
    """Valid PDF base64 → returns bytes."""
    pdf_data = b"%PDF-1.4 fake pdf content"
    pdf_b64 = base64.b64encode(pdf_data).decode()
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"pdf_base64": pdf_b64})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    result = download_pdf_with_browser(command="echo", pdf_url="https://x.com/a.pdf")
    assert result == pdf_data


# ── discover_pdf_url_with_browser ───────────────────────────────

def test_discover_pdf_nonzero_rc(monkeypatch) -> None:
    """Non-zero return → None."""
    class FakeResult:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert discover_pdf_url_with_browser(command="echo", page_url="https://x.com") is None


def test_discover_pdf_empty_stdout(monkeypatch) -> None:
    """Empty stdout → None."""
    class FakeResult:
        returncode = 0
        stdout = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert discover_pdf_url_with_browser(command="echo", page_url="https://x.com") is None


def test_discover_pdf_plain_url_response(monkeypatch) -> None:
    """JSON parse fails but stdout is a URL → returns it."""
    class FakeResult:
        returncode = 0
        stdout = "https://example.com/paper.pdf"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    result = discover_pdf_url_with_browser(command="echo", page_url="https://x.com")
    assert result == "https://example.com/paper.pdf"


def test_discover_pdf_plain_non_url(monkeypatch) -> None:
    """JSON parse fails and stdout is not a URL → None."""
    class FakeResult:
        returncode = 0
        stdout = "just some text"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert discover_pdf_url_with_browser(command="echo", page_url="https://x.com") is None


def test_discover_pdf_not_dict(monkeypatch) -> None:
    """JSON valid but not a dict → None."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps([1, 2, 3])
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert discover_pdf_url_with_browser(command="echo", page_url="https://x.com") is None


def test_discover_pdf_missing_pdf_url_key(monkeypatch) -> None:
    """Dict without pdf_url → None."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"not_pdf": 1})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert discover_pdf_url_with_browser(command="echo", page_url="https://x.com") is None


def test_discover_pdf_empty_pdf_url(monkeypatch) -> None:
    """pdf_url is empty string → None."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"pdf_url": "  "})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    assert discover_pdf_url_with_browser(command="echo", page_url="https://x.com") is None


def test_discover_pdf_success(monkeypatch) -> None:
    """Valid pdf_url → returned."""
    class FakeResult:
        returncode = 0
        stdout = json.dumps({"pdf_url": "https://x.com/paper.pdf"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    result = discover_pdf_url_with_browser(command="echo", page_url="https://x.com")
    assert result == "https://x.com/paper.pdf"
