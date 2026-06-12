from pzi.capture_core import capture_to_bib
from pzi.capture_models import (
    AuthHints,
    CaptureInput,
    CaptureOptions,
    PageArtifact,
    PdfCandidate,
)


def test_capture_to_bib_maps_model_to_add_service_kwargs() -> None:
    calls = []

    def fake_add_input_to_bib(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "citekey": "smith2024graph"}

    result = capture_to_bib(
        CaptureInput(
            value="https://example.com/paper",
            record_overrides={"title": "Graph Parsers"},
            bib_selector="ml",
            pdf_candidates=(
                PdfCandidate("https://example.com/b.pdf", source="page"),
                PdfCandidate("https://example.com/a.pdf", source="cli"),
            ),
            auth_hints=AuthHints(cookies="sid=123"),
        ),
        CaptureOptions(dry_run=True, force_new=True),
        config_path="/tmp/config.toml",
        home_dir="/tmp",
        add_fn=fake_add_input_to_bib,
    )

    assert result == {"status": "ok", "citekey": "smith2024graph"}
    assert calls == [
        {
            "config_path": "/tmp/config.toml",
            "home_dir": "/tmp",
            "value": "https://example.com/paper",
            "record_overrides": {"title": "Graph Parsers"},
            "bib_selector": "ml",
            "dry_run": True,
            "force_new": True,
            # ranked: cli (10) > page (7), so a.pdf before b.pdf
            "pdf_url_candidates": [
                "https://example.com/a.pdf",
                "https://example.com/b.pdf",
            ],
            "cookies": "sid=123",
        }
    ]


def test_capture_to_bib_dedupes_normalizes_and_ranks_pdf_candidates() -> None:
    calls = []

    def fake_add_input_to_bib(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    capture_to_bib(
        CaptureInput(
            value="https://example.com/paper",
            pdf_candidates=(
                PdfCandidate(
                    "https://example.com/a.pdf", source="page", confidence=40
                ),
                PdfCandidate(
                    "https://example.com/a.pdf", source="cli", confidence=90
                ),
                PdfCandidate(
                    "/tmp/local.pdf", source="page", kind="path", confidence=20
                ),
            ),
        ),
        CaptureOptions(),
        config_path="cfg.toml",
        home_dir="/home/me",
        add_fn=fake_add_input_to_bib,
    )

    # deduped: a.pdf keeps cli (90 > 40)
    # ranked: path > url, then source priority, then confidence
    assert calls[0]["pdf_url_candidates"] == [
        "/tmp/local.pdf",             # kind=path first
        "https://example.com/a.pdf",  # url, source=cli, conf=90
    ]


def test_capture_to_bib_omits_empty_optional_kwargs() -> None:
    calls = []

    def fake_add_input_to_bib(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    capture_to_bib(
        CaptureInput(value="10.1234/foo"),
        CaptureOptions(),
        config_path="cfg.toml",
        home_dir="/home/me",
        add_fn=fake_add_input_to_bib,
    )

    assert calls == [
        {
            "config_path": "cfg.toml",
            "home_dir": "/home/me",
            "value": "10.1234/foo",
            "record_overrides": {},
            "bib_selector": None,
            "dry_run": False,
            "force_new": False,
        }
    ]


def test_capture_to_bib_adds_page_html_metadata_as_fallbacks() -> None:
    calls = []

    def fake_add_input_to_bib(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    capture_to_bib(
        CaptureInput(
            value="https://example.com/paper",
            record_overrides={"title": "CLI Title"},
            page_artifact=PageArtifact(
                html="""
                <meta name="citation_title" content="HTML Title">
                <meta name="citation_author" content="Smith, Jane">
                <meta name="citation_year" content="2024">
                <meta name="citation_pdf_url" content="https://example.com/paper.pdf">
                """,
                source="file",
            ),
        ),
        CaptureOptions(),
        config_path="cfg.toml",
        home_dir="/home/me",
        add_fn=fake_add_input_to_bib,
    )

    assert calls[0]["record_overrides"] == {
        "title": "CLI Title",
        "fallback_title": "HTML Title",
        "fallback_authors": ["Smith, Jane"],
        "fallback_year": 2024,
        "fallback_pdf_url": "https://example.com/paper.pdf",
    }
    assert calls[0]["pdf_url_candidates"] == ["https://example.com/paper.pdf"]


def test_capture_to_bib_adds_external_page_metadata_as_fallbacks() -> None:
    calls = []
    command_calls = []

    def fake_add_input_to_bib(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    def fake_page_metadata_cmd(command, *, url, html, current_metadata, timeout_seconds):
        command_calls.append(
            {
                "command": command,
                "url": url,
                "html": html,
                "current_metadata": current_metadata,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "title": "External Title",
            "pdf_url": "https://example.com/external.pdf",
        }

    capture_to_bib(
        CaptureInput(
            value="https://example.com/paper",
            record_overrides={"title": "CLI Title"},
            page_artifact=PageArtifact(html="<html></html>", source="file"),
        ),
        CaptureOptions(page_metadata_cmd="metadata-tool", page_metadata_timeout_seconds=7),
        config_path="cfg.toml",
        home_dir="/home/me",
        add_fn=fake_add_input_to_bib,
        page_metadata_cmd_fn=fake_page_metadata_cmd,
    )

    assert command_calls == [
        {
            "command": "metadata-tool",
            "url": "https://example.com/paper",
            "html": "<html></html>",
            "current_metadata": {},
            "timeout_seconds": 7,
        }
    ]
    assert calls[0]["record_overrides"] == {
        "title": "CLI Title",
        "fallback_title": "External Title",
        "fallback_pdf_url": "https://example.com/external.pdf",
    }
    assert calls[0]["pdf_url_candidates"] == ["https://example.com/external.pdf"]
