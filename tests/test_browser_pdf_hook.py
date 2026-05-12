import base64
import io
import json
import sys

from pzi import browser_pdf_hook as hook


def test_parse_hook_request_rejects_non_dict() -> None:
    assert hook.parse_hook_request(["nope"]) is None


def test_parse_hook_request_normalizes_discover_url() -> None:
    assert hook.parse_hook_request({"page_url": " https://example.test/article "}) == (
        "discover",
        "https://example.test/article",
    )


def test_parse_hook_request_normalizes_download_url() -> None:
    assert hook.parse_hook_request(
        {"action": "download_pdf", "pdf_url": " https://example.test/paper.pdf "}
    ) == ("download_pdf", "https://example.test/paper.pdf")


def test_parse_hook_request_rejects_missing_urls() -> None:
    assert hook.parse_hook_request({"page_url": "   "}) is None
    assert hook.parse_hook_request({"action": "download_pdf", "pdf_url": None}) is None


def test_encode_hook_response_empty_pdf_url_and_bytes() -> None:
    assert hook.encode_hook_response() == "{}"
    assert hook.encode_hook_response(pdf_url="") == "{}"
    assert hook.encode_hook_response(pdf_bytes=b"") == "{}"


def test_encode_hook_response_pdf_url() -> None:
    assert json.loads(hook.encode_hook_response(pdf_url="https://example.test/paper.pdf")) == {
        "pdf_url": "https://example.test/paper.pdf"
    }


def test_encode_hook_response_pdf_bytes() -> None:
    encoded = json.loads(hook.encode_hook_response(pdf_bytes=b"%PDF-test"))["pdf_base64"]
    assert base64.b64decode(encoded) == b"%PDF-test"


def test_browser_launch_options_for_firefox_disable_pdf_viewer() -> None:
    assert hook.browser_launch_options("chromium") == {"headless": True}
    assert hook.browser_launch_options("firefox") == {
        "headless": True,
        "firefox_user_prefs": {
            "browser.download.folderList": 2,
            "browser.download.manager.showWhenStarting": False,
            "pdfjs.disabled": True,
        },
    }


def test_resolve_pdf_candidate_urls_filters_normalizes_and_deduplicates() -> None:
    assert hook.resolve_pdf_candidate_urls(
        "https://journal.test/articles/1",
        [
            " /files/paper.pdf ",
            "/files/paper.pdf",
            "https://journal.test/download?id=1",
            "mailto:editor@example.test",
            None,
            "https://journal.test/supplement.html",
        ],
    ) == [
        "https://journal.test/files/paper.pdf",
        "https://journal.test/download?id=1",
    ]


def test_main_discovers_pdf_url(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["pzi-browser-hook", "--browser", "firefox", "--profile", "prof"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"page_url":"https://example.test/a"}'))
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: True)
    monkeypatch.setattr(
        hook,
        "discover_pdf_url",
        lambda page_url, *, browser, profile_path: f"{page_url}/paper.pdf"
        if browser == "firefox" and profile_path == "prof"
        else None,
    )

    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "pdf_url": "https://example.test/a/paper.pdf"
    }


def test_main_downloads_pdf(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi-browser-hook"])
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO('{"action":"download_pdf","pdf_url":"https://example.test/p.pdf"}'),
    )
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: True)
    monkeypatch.setattr(
        hook,
        "download_pdf",
        lambda pdf_url, *, browser, profile_path: b"%PDF-test",
    )

    assert hook.main() == 0
    encoded = json.loads(capsys.readouterr().out)["pdf_base64"]
    assert base64.b64decode(encoded) == b"%PDF-test"


def test_main_returns_empty_for_bad_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi-browser-hook"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: True)

    assert hook.main() == 0
    assert capsys.readouterr().out.strip() == "{}"


def test_main_returns_error_when_browser_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["pzi-browser-hook"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(hook, "_ensure_browser", lambda browser: False)

    assert hook.main() == 1
    assert capsys.readouterr().out.strip() == "{}"
