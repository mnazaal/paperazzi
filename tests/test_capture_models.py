from pathlib import Path

from pzi.capture_models import (
    CaptureInput,
    PageArtifact,
    PdfCandidate,
    load_page_artifact,
    normalize_pdf_candidates,
    rank_pdf_candidates,
)


def test_load_page_artifact_reads_html_file(tmp_path: Path) -> None:
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><title>Graph Parsers</title></html>")

    artifact = load_page_artifact(str(html_path), stdin_text=None)

    assert artifact == PageArtifact(
        html="<html><title>Graph Parsers</title></html>",
        source="file",
        path=str(html_path),
    )


def test_capture_options_keeps_ordered_pdf_candidates() -> None:
    capture = CaptureInput(
        value="https://example.com/paper",
        pdf_candidates=(
            PdfCandidate(value="https://example.com/a.pdf", source="cli"),
            PdfCandidate(value="https://example.com/b.pdf", source="page"),
        )
    )

    assert [candidate.value for candidate in capture.pdf_candidates] == [
        "https://example.com/a.pdf",
        "https://example.com/b.pdf",
    ]


def test_pdf_candidate_has_rich_fields() -> None:
    candidate = PdfCandidate(
        value="https://example.com/paper.pdf",
        source="browser",
        kind="url",
        confidence=85,
        requires_cookies=False,
        requires_permission=True,
    )

    assert candidate.value == "https://example.com/paper.pdf"
    assert candidate.source == "browser"
    assert candidate.kind == "url"
    assert candidate.confidence == 85
    assert candidate.requires_cookies is False
    assert candidate.requires_permission is True


def test_pdf_candidate_defaults() -> None:
    candidate = PdfCandidate(value="https://example.com/paper.pdf")

    assert candidate.kind == "url"
    assert candidate.confidence == 50
    assert candidate.requires_cookies is False
    assert candidate.requires_permission is False


def test_normalize_pdf_candidates_dedupes_by_value_keeps_highest_confidence() -> None:
    candidates = [
        PdfCandidate(value="https://example.com/a.pdf", source="cli", confidence=60),
        PdfCandidate(value="https://example.com/a.pdf", source="page", confidence=80),
        PdfCandidate(value="https://example.com/b.pdf", source="page", confidence=50),
    ]

    result = normalize_pdf_candidates(candidates)

    values = [c.value for c in result]
    assert values == ["https://example.com/a.pdf", "https://example.com/b.pdf"]
    # The deduped "a.pdf" should be the page one (higher confidence, 80 > 60)
    assert result[0].confidence == 80
    assert result[0].source == "page"


def test_normalize_pdf_candidates_preserves_first_when_same_confidence() -> None:
    candidates = [
        PdfCandidate(value="https://example.com/a.pdf", source="cli", confidence=50),
        PdfCandidate(value="https://example.com/a.pdf", source="page", confidence=50),
    ]

    result = normalize_pdf_candidates(candidates)

    assert len(result) == 1
    assert result[0].source == "cli"  # first wins on tie


def test_normalize_pdf_candidates_returns_tuple() -> None:
    result = normalize_pdf_candidates([])
    assert isinstance(result, tuple)
    assert len(result) == 0


def test_rank_pdf_candidates_sorts_by_kind_then_source_priority_then_confidence() -> None:
    candidates = [
        PdfCandidate(value="https://example.com/b.pdf", source="page", kind="url", confidence=50),
        PdfCandidate(value="/tmp/local.pdf", source="cli", kind="path", confidence=30),
        PdfCandidate(value="https://example.com/a.pdf", source="translation", kind="url", confidence=90),
        PdfCandidate(value="https://example.com/c.pdf", source="page", kind="url", confidence=80),
    ]

    ranked = rank_pdf_candidates(candidates)

    values = [c.value for c in ranked]
    # path before url, then source priority, then confidence desc
    assert values == [
        "/tmp/local.pdf",              # kind=path, source=cli, conf=30
        "https://example.com/a.pdf",   # kind=url, source=translation, conf=90
        "https://example.com/c.pdf",   # kind=url, source=page, conf=80
        "https://example.com/b.pdf",   # kind=url, source=page, conf=50
    ]


def test_rank_pdf_candidates_returns_tuple() -> None:
    result = rank_pdf_candidates([])
    assert isinstance(result, tuple)
