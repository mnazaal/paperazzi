from pathlib import Path

from pzi.pdf import _wait_for_stable_file
from pzi.pdf_planning import _desktop_timeout


def test_desktop_browser_timeout_defaults_and_clamps() -> None:
    assert _desktop_timeout(None) == 300
    assert _desktop_timeout("5") == 30
    assert _desktop_timeout("bad") == 300
    assert _desktop_timeout("120") == 120


def test_wait_for_stable_file_returns_true_for_unchanged_file(tmp_path: Path) -> None:
    target = tmp_path / "paper.pdf"
    target.write_bytes(b"%PDF-stable")

    assert _wait_for_stable_file(target, stable_seconds=0.01)


def test_wait_for_stable_file_returns_false_for_missing_file(tmp_path: Path) -> None:
    assert not _wait_for_stable_file(tmp_path / "missing.pdf", stable_seconds=0.01)


def test_fallback_settings_are_injectable_without_touching_the_environment() -> None:
    """The fallback knobs are values, not process-environment reads.

    They used to be read via os.environ deep inside the fallback chain, so the
    effective configuration could not be reconstructed from what a run was given
    and a test could only steer them by mutating the environment.
    """
    from pzi.pdf_planning import PdfFallbackSettings

    settings = PdfFallbackSettings.from_environment(
        {
            "PZI_DISABLE_DESKTOP_BROWSER_FALLBACK": "1",
            "PZI_DOWNLOAD_DIR": "/tmp/pzi-downloads",
            "PZI_DESKTOP_BROWSER_TIMEOUT": "5",
            "PZI_BROWSER": "chromium",
        }
    )

    assert settings.disable_desktop_browser is True
    assert str(settings.download_dir) == "/tmp/pzi-downloads"
    assert settings.desktop_timeout == 30  # floor applied
    assert settings.browser == "chromium"

    # An explicitly disabled desktop fallback short-circuits, no environment involved.
    from pzi.pdf import fetch_pdf_via_desktop_browser_download

    path, error = fetch_pdf_via_desktop_browser_download(
        url="https://example.com/paper.pdf",
        papers_dir="/tmp",
        citekey="smith2024",
        settings=settings,
    )
    assert (path, error) == (None, None)
