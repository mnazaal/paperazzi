import json
from pathlib import Path

import pytest

from pzi.pdf import (
    fetch_and_store_pdf_with_fallbacks,
    fetch_unpaywall_pdf_url,
    write_pdf_bytes,
)
from pzi.pdf_download import (
    copy_pdf_to_papers_dir,
    fetch_and_store_pdf,
)
from pzi.pdf_planning import is_pdf_bytes, plan_pdf_path


def test_is_pdf_bytes_detects_pdf_signature() -> None:
    assert is_pdf_bytes(b"%PDF-1.7\nrest") is True
    assert is_pdf_bytes(b"<html>not pdf</html>") is False


def test_plan_pdf_path_uses_deterministic_citekey_name() -> None:
    assert (
        plan_pdf_path(papers_dir="/tmp/papers", citekey="smith2024graph")
        == "/tmp/papers/smith2024graph.pdf"
    )


def test_write_pdf_bytes_creates_parent_and_overwrites_atomically(tmp_path: Path) -> None:
    path = write_pdf_bytes(
        data=b"%PDF-first",
        papers_dir=str(tmp_path / "nested" / "papers"),
        citekey="smith2024graph",
    )
    path_again = write_pdf_bytes(
        data=b"%PDF-second",
        papers_dir=str(tmp_path / "nested" / "papers"),
        citekey="smith2024graph",
    )

    assert path != path_again  # different content gets a new path
    assert Path(path).read_bytes() == b"%PDF-first"
    assert Path(path_again).read_bytes() == b"%PDF-second"


def test_copy_pdf_to_papers_dir_copies_valid_pdf(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-local")

    path, error = copy_pdf_to_papers_dir(
        source_path=str(source), papers_dir=str(tmp_path / "papers"), citekey="local2024"
    )

    assert error is None
    assert path == str(tmp_path / "papers" / "local2024.pdf")
    assert (tmp_path / "papers" / "local2024.pdf").read_bytes() == b"%PDF-local"


def test_copy_pdf_to_papers_dir_reports_missing_source(tmp_path: Path) -> None:
    path, error = copy_pdf_to_papers_dir(
        source_path=str(tmp_path / "missing.pdf"),
        papers_dir=str(tmp_path / "papers"),
        citekey="missing2024",
    )

    assert path is None
    assert error == f"source PDF not found: {tmp_path / 'missing.pdf'}"


def test_copy_pdf_to_papers_dir_rejects_non_pdf(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"not pdf")

    path, error = copy_pdf_to_papers_dir(
        source_path=str(source), papers_dir=str(tmp_path / "papers"), citekey="bad2024"
    )

    assert path is None
    assert error == f"source file is not a valid PDF: {source}"


def test_copy_pdf_to_papers_dir_reports_read_error(tmp_path: Path) -> None:
    path, error = copy_pdf_to_papers_dir(
        source_path=str(tmp_path), papers_dir=str(tmp_path / "papers"), citekey="dir2024"
    )

    assert path is None
    assert error is not None
    assert error.startswith("failed to read source PDF:")


def test_store_pdf_source_uses_download_for_urls(tmp_path: Path) -> None:
    from pzi.pdf_service import _store_pdf_source as store_pdf_source

    path, error = store_pdf_source(
        source="https://example.com/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="remote2024",
        fetch_binary=lambda url: (b"%PDF-remote", "application/pdf"),
    )

    assert error is None
    assert path == str(tmp_path / "remote2024.pdf")
    assert (tmp_path / "remote2024.pdf").read_bytes() == b"%PDF-remote"


def test_store_pdf_source_uses_copy_for_local_paths(tmp_path: Path) -> None:
    from pzi.pdf_service import _store_pdf_source as store_pdf_source

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-local-source")

    path, error = store_pdf_source(
        source=str(source),
        papers_dir=str(tmp_path / "papers"),
        citekey="local2024",
    )

    assert error is None
    assert path == str(tmp_path / "papers" / "local2024.pdf")
    assert (tmp_path / "papers" / "local2024.pdf").read_bytes() == b"%PDF-local-source"


def test_fetch_and_store_pdf_writes_valid_pdf(tmp_path: Path) -> None:
    path, warning = fetch_and_store_pdf(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/pdf"),
    )

    assert warning is None
    assert path == str(tmp_path / "smith2024graph.pdf")
    assert (tmp_path / "smith2024graph.pdf").read_bytes() == b"%PDF-1.7\nbody"


def test_fetch_and_store_pdf_accepts_ambiguous_content_type_with_pdf_bytes(
    tmp_path: Path,
) -> None:
    path, warning = fetch_and_store_pdf(
        url="https://example.com/download",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"%PDF-1.7\nbody", "application/octet-stream"),
    )

    assert warning is None
    assert path == str(tmp_path / "smith2024graph.pdf")


def test_fetch_and_store_pdf_reports_download_error(tmp_path: Path) -> None:
    def failing_fetch(url: str) -> tuple[bytes, str | None]:
        raise OSError("network down")

    path, error = fetch_and_store_pdf(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=failing_fetch,
    )

    assert path is None
    assert error == "failed to download PDF from https://example.com/paper.pdf: network down"


def test_fetch_unpaywall_pdf_url_returns_pdf_url() -> None:
    payload = json.dumps(
        {"best_oa_location": {"url_for_pdf": "https://arxiv.org/pdf/2301.07041"}}
    )
    calls: list[str] = []

    def fake_fetch_text(url: str) -> str:
        calls.append(url)
        return payload

    result = fetch_unpaywall_pdf_url(
        "10.1145/1327452.1327492", email="test@example.com", fetch_text=fake_fetch_text
    )

    assert result == "https://arxiv.org/pdf/2301.07041"
    assert "10.1145%2F1327452.1327492" in calls[0]
    assert "test%40example.com" in calls[0]


def test_fetch_unpaywall_pdf_url_returns_none_when_no_oa_location() -> None:
    payload = json.dumps({"best_oa_location": None})

    result = fetch_unpaywall_pdf_url(
        "10.1234/paywalled", email="x@x.com", fetch_text=lambda _: payload
    )

    assert result is None


def test_fetch_unpaywall_pdf_url_returns_none_on_error() -> None:
    def failing_fetch(url: str) -> str:
        raise OSError("network down")

    result = fetch_unpaywall_pdf_url(
        "10.1234/foo", email="x@x.com", fetch_text=failing_fetch
    )

    assert result is None


def test_fetch_and_store_pdf_rejects_html_content(tmp_path: Path) -> None:
    path, warning = fetch_and_store_pdf(
        url="https://example.com/snapshot",
        papers_dir=str(tmp_path),
        citekey="smith2024graph",
        fetch_binary=lambda url: (b"<html>snapshot</html>", "text/html"),
    )

    assert path is None
    assert (
        "downloaded content from https://example.com/snapshot is HTML, not a PDF"
        in warning
    )
    assert list(tmp_path.iterdir()) == []


def test_fetch_and_store_pdf_with_fallbacks_uses_direct_download_first(tmp_path: Path) -> None:
    call_order = []

    def mock_fetch_binary(url: str) -> tuple[bytes, str | None]:
        call_order.append("direct")
        return (b"%PDF-1.4 test content", "application/pdf")

    local_path, warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="test2024paper",
        fetch_binary=mock_fetch_binary,
        flaresolverr_url="http://127.0.0.1:8191",
        browser_pdf_cmd="echo",
    )

    assert local_path is not None
    assert warning is None
    assert error is None
    assert call_order == ["direct"]
    assert (tmp_path / "papers" / "test2024paper.pdf").exists()


def test_fetch_and_store_pdf_with_fallbacks_returns_error_when_all_fail(tmp_path: Path) -> None:
    def mock_fetch_binary(url: str) -> tuple[bytes, str | None]:
        raise OSError("connection refused")

    local_path, warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="test2024paper",
        fetch_binary=mock_fetch_binary,
        flaresolverr_url=None,  # No FlareSolverr
        browser_pdf_cmd=None,  # No browser hook
    )

    assert local_path is None
    assert warning is None
    assert error is not None
    assert "all download methods failed" in error


def test_fetch_and_store_pdf_with_fallbacks_uses_browser_after_direct_fail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import pzi.browser_pdf

    def mock_fetch_binary(url: str) -> tuple[bytes, str | None]:
        return (b"<html>blocked</html>", "text/html")

    monkeypatch.setattr(
        pzi.browser_pdf,
        "download_pdf_with_browser",
        lambda *, command, pdf_url: b"%PDF-browser",
    )

    local_path, warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="browser2024",
        fetch_binary=mock_fetch_binary,
        browser_pdf_cmd="hook --profile prof",
    )

    assert warning is None
    assert error is None
    assert local_path == str(tmp_path / "papers" / "browser2024.pdf")
    assert (tmp_path / "papers" / "browser2024.pdf").read_bytes() == b"%PDF-browser"


def test_fetch_and_store_pdf_with_fallbacks_uses_flaresolverr_after_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import pzi.browser_pdf
    import pzi.flaresolverr

    def mock_fetch_binary(url: str) -> tuple[bytes, str | None]:
        return (b"<html>blocked</html>", "text/html")

    monkeypatch.setattr(
        pzi.browser_pdf,
        "download_pdf_with_browser",
        lambda *, command, pdf_url: b"not pdf",
    )
    monkeypatch.setattr(
        pzi.flaresolverr,
        "fetch_pdf_via_flaresolverr",
        lambda url, *, server_url: b"%PDF-flare",
    )

    local_path, warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="flare2024",
        fetch_binary=mock_fetch_binary,
        browser_pdf_cmd="hook --profile prof",
        flaresolverr_url="http://127.0.0.1:8191",
    )

    assert error is None
    assert warning is not None
    assert "PDF downloaded via FlareSolverr" in warning
    assert local_path == str(tmp_path / "papers" / "flare2024.pdf")
    assert (tmp_path / "papers" / "flare2024.pdf").read_bytes() == b"%PDF-flare"


def test_fetch_and_store_pdf_with_fallbacks_survives_a_raising_browser_step(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A misconfigured browser hook must not skip the remaining fallbacks.

    The chain advances on a falsy return and has no exception handling of its
    own, so every step is required never to raise. When the browser step raised
    — a missing binary, or an unbalanced quote in `browser_pdf_cmd` — FlareSolverr
    and the desktop fallback were never reached.
    """
    import pzi.browser_pdf
    import pzi.flaresolverr

    def mock_fetch_binary(url: str) -> tuple[bytes, str | None]:
        return (b"<html>blocked</html>", "text/html")

    # Fail at the subprocess boundary, leaving the real `download_pdf_with_browser`
    # in place — stubbing that function out would test the stub, not the fix.
    def exploding_run(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(pzi.browser_pdf.subprocess, "run", exploding_run)
    monkeypatch.setattr(
        pzi.flaresolverr,
        "fetch_pdf_via_flaresolverr",
        lambda url, *, server_url: b"%PDF-flare",
    )

    local_path, warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://example.com/paper.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="flare2024",
        fetch_binary=mock_fetch_binary,
        browser_pdf_cmd="/nonexistent/hook --profile prof",
        flaresolverr_url="http://127.0.0.1:8191",
    )

    # The chain continued to FlareSolverr instead of aborting.
    assert error is None
    assert local_path == str(tmp_path / "papers" / "flare2024.pdf")
    assert (tmp_path / "papers" / "flare2024.pdf").read_bytes() == b"%PDF-flare"


@pytest.mark.parametrize("rung", ["browser_pdf_cmd", "desktop"])
def test_resolved_settings_reach_every_fallback_rung(
    rung: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Every rung uses the *passed* settings, not a fresh read of os.environ.

    ``fetch_and_store_pdf_with_fallbacks`` resolves ``PdfFallbackSettings`` once
    and documents that the helpers no longer read the environment themselves.
    Two rungs dropped the keyword and re-resolved, so a caller passing
    ``disable_desktop_browser=True`` was ignored. The environment here says the
    opposite of the injected settings at both rungs, so a rung that re-reads it
    is visible in the assertion.
    """
    import pzi.browser_pdf
    import pzi.pdf
    from pzi.pdf_planning import PdfFallbackSettings

    monkeypatch.setenv("PZI_BROWSER_PDF_CMD", "env-hook")
    monkeypatch.setenv("PZI_DISABLE_DESKTOP_BROWSER_FALLBACK", "0")
    monkeypatch.setenv("PZI_DOWNLOAD_DIR", str(tmp_path / "env-downloads"))
    settings = PdfFallbackSettings(
        disable_desktop_browser=True,
        download_dir=tmp_path / "settings-downloads",
        browser_pdf_cmd="settings-hook",
    )

    commands: list[str] = []

    def record_command(*, command: str, pdf_url: str) -> bytes | None:
        commands.append(command)
        return None

    monkeypatch.setattr(pzi.browser_pdf, "download_pdf_with_browser", record_command)
    # If the desktop rung re-resolves from the environment it will find the
    # fallback enabled and try to open a browser; make that observable rather
    # than interactive.
    monkeypatch.setattr(pzi.pdf.webbrowser, "open", lambda url: False)

    local_path, _warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1.full.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="settings2024",
        fetch_binary=lambda url: (b"<html>blocked</html>", "text/html"),
        settings=settings,
    )

    assert local_path is None
    assert error is not None
    if rung == "browser_pdf_cmd":
        assert commands == ["settings-hook"]
    else:
        assert "desktop browser download: skipped" in error


def test_desktop_fallback_reports_an_unwritable_download_dir_instead_of_raising(
    tmp_path: Path,
) -> None:
    """The last rung reports failure by returning None, like every other rung.

    ``fetch_and_store_pdf_with_fallbacks`` has no exception handling, so an
    OSError from ``download_dir.mkdir`` escaped the whole chain and discarded
    the stage errors the earlier rungs had already recorded.
    """
    from pzi.pdf import fetch_pdf_via_desktop_browser_download
    from pzi.pdf_planning import PdfFallbackSettings

    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")

    path, reason = fetch_pdf_via_desktop_browser_download(
        url="https://www.biorxiv.org/content/paper.full.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="blocked2024",
        settings=PdfFallbackSettings(download_dir=blocker / "downloads"),
    )

    assert path is None
    assert reason is not None
    assert reason.startswith("desktop download dir unavailable: ")


def test_unwritable_download_dir_keeps_the_earlier_stage_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The chain still reports what the earlier rungs learned."""
    import pzi.browser_pdf
    from pzi.pdf_planning import PdfFallbackSettings

    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    monkeypatch.setattr(
        pzi.browser_pdf, "download_pdf_with_browser", lambda *, command, pdf_url: None
    )

    local_path, _warning, error = fetch_and_store_pdf_with_fallbacks(
        url="https://www.biorxiv.org/content/10.1101/2024.01.01.123456v1.full.pdf",
        papers_dir=str(tmp_path / "papers"),
        citekey="blocked2024",
        fetch_binary=lambda url: (b"<html>blocked</html>", "text/html"),
        settings=PdfFallbackSettings(download_dir=blocker / "downloads"),
    )

    assert local_path is None
    assert error is not None
    assert "direct download: " in error
    assert "browser_pdf_cmd: no PDF returned" in error
    assert "desktop download dir unavailable: " in error
