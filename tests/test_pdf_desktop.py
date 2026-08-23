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
    assert path is None
    # It says it was skipped rather than returning a bare `None, None`: the
    # caller appends the second value as a stage error, so "no PDF appeared"
    # told a user who had switched the stage off that it had been tried.
    assert error == "skipped (PZI_DISABLE_DESKTOP_BROWSER_FALLBACK is set)"


def test_env_flag_treats_zero_and_false_as_off() -> None:
    """`bool("0")` is True, so `PZI_SKIP_BROWSER_HOOK=0` *enabled* the skip.

    README documents "set to 1 to skip", which implies 0 does not — so the
    obvious way to turn these off did the opposite.
    """
    from pzi.pdf_planning import env_flag

    for off in (None, "", "0", "false", "False", "no", "off", "  0  "):
        assert env_flag(off) is False, off
    for on in ("1", "true", "yes", "on", "anything"):
        assert env_flag(on) is True, on


def test_pdf_fallback_settings_do_not_skip_when_flags_are_set_to_zero() -> None:
    from pzi.pdf_planning import PdfFallbackSettings

    settings = PdfFallbackSettings.from_environment(
        {"PZI_SKIP_BROWSER_HOOK": "0", "PZI_DISABLE_DESKTOP_BROWSER_FALLBACK": "0"}
    )

    assert settings.skip_browser_hook is False
    assert settings.disable_desktop_browser is False


def test_pdf_fallback_settings_still_skip_when_flags_are_set_to_one() -> None:
    from pzi.pdf_planning import PdfFallbackSettings

    settings = PdfFallbackSettings.from_environment(
        {"PZI_SKIP_BROWSER_HOOK": "1", "PZI_DISABLE_DESKTOP_BROWSER_FALLBACK": "1"}
    )

    assert settings.skip_browser_hook is True
    assert settings.disable_desktop_browser is True


# ---------------------------------------------------------------------------
# _newest_first — the scan that races the browser writing into the directory
# ---------------------------------------------------------------------------


def test_newest_first_orders_by_mtime(tmp_path: Path) -> None:
    import os

    from pzi.pdf import _newest_first

    older = tmp_path / "older.pdf"
    newer = tmp_path / "newer.pdf"
    older.write_bytes(b"%PDF-1")
    newer.write_bytes(b"%PDF-2")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    assert _newest_first(tmp_path) == [newer, older]


def test_newest_first_skips_a_candidate_that_vanishes_mid_scan(tmp_path: Path) -> None:
    """The documented crash: a partial download renamed between glob and sort.

    ``sorted``'s key used to raise ``FileNotFoundError`` out of the scan, killing
    the fallback at the moment it was about to succeed. A dangling symlink is the
    same observation — glob lists it, ``stat`` raises.
    """
    from pzi.pdf import _newest_first

    real = tmp_path / "real.pdf"
    real.write_bytes(b"%PDF-1")
    (tmp_path / "vanished.pdf").symlink_to(tmp_path / "gone.pdf")

    assert _newest_first(tmp_path) == [real]


# ---------------------------------------------------------------------------
# fetch_pdf_via_desktop_browser_download — the watch loop
# ---------------------------------------------------------------------------


def _desktop_settings(download_dir: Path):
    from pzi.pdf_planning import PdfFallbackSettings

    return PdfFallbackSettings(download_dir=download_dir, desktop_timeout=30)


def _run_desktop_fallback(
    *,
    tmp_path: Path,
    monkeypatch,
    plant: bytes | None = b"%PDF-planted",
    plant_name: str = "smith2024.pdf",
    opened: bool = True,
    timeout: int = 3,
    url: str = "https://www.biorxiv.org/content/smith2024.full.pdf",
    citekey: str = "smith2024",
    pre_existing: bool = False,
):
    """Drive the watch loop with a planted download and no real sleeping."""
    import pzi.pdf
    from pzi.pdf import fetch_pdf_via_desktop_browser_download

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    if pre_existing and plant is not None:
        (downloads / plant_name).write_bytes(plant)

    monkeypatch.setattr(pzi.pdf.webbrowser, "open", lambda _url: opened)

    clock = {"now": 0.0}
    planted: list[bool] = []

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds
        # The browser "finishes" its download once, on the first pass through
        # the loop; writing it again on every tick would keep the file forever
        # unstable and the loop would never accept it.
        if not pre_existing and plant is not None and not planted:
            (downloads / plant_name).write_bytes(plant)
            planted.append(True)

    return fetch_pdf_via_desktop_browser_download(
        url=url,
        papers_dir=str(tmp_path / "papers"),
        citekey=citekey,
        timeout=timeout,
        settings=_desktop_settings(downloads),
        sleep=fake_sleep,
        monotonic=lambda: clock["now"],
    )


def test_desktop_fallback_imports_a_matching_download(tmp_path: Path, monkeypatch) -> None:
    path, warning = _run_desktop_fallback(tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert path == str(tmp_path / "papers" / "smith2024.pdf")
    assert Path(path).read_bytes() == b"%PDF-planted"
    assert warning is not None
    # The warning names the actual host rather than a hardcoded preprint server.
    assert "biorxiv.org" in warning


def test_desktop_fallback_refuses_a_non_http_url(tmp_path: Path) -> None:
    from pzi.pdf import fetch_pdf_via_desktop_browser_download

    path, reason = fetch_pdf_via_desktop_browser_download(
        url="file:///etc/passwd",
        papers_dir=str(tmp_path / "papers"),
        citekey="smith2024",
        settings=_desktop_settings(tmp_path / "downloads"),
    )

    assert path is None
    assert reason is not None
    assert reason.startswith("refusing to open a non-http(s) URL in the browser")
    # Refused before the directory is even created.
    assert not (tmp_path / "downloads").exists()


def test_desktop_fallback_reports_a_browser_that_would_not_open(
    tmp_path: Path, monkeypatch
) -> None:
    path, reason = _run_desktop_fallback(
        tmp_path=tmp_path, monkeypatch=monkeypatch, opened=False
    )

    assert path is None
    # Not "no PDF appeared": nothing was ever watched for.
    assert reason == "could not open a desktop browser"


def test_desktop_fallback_ignores_a_pdf_that_was_already_in_the_directory(
    tmp_path: Path, monkeypatch
) -> None:
    path, reason = _run_desktop_fallback(
        tmp_path=tmp_path, monkeypatch=monkeypatch, pre_existing=True, timeout=1
    )

    assert path is None
    assert reason is None


def test_desktop_fallback_ignores_an_unrelated_download(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path, reason = _run_desktop_fallback(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        plant_name="some-other-paper-entirely.pdf",
        timeout=3,
    )

    assert path is None
    assert reason is None
    assert "Ignoring unrelated desktop browser PDF download" in capsys.readouterr().err


def test_desktop_fallback_ignores_a_file_whose_bytes_are_not_a_pdf(
    tmp_path: Path, monkeypatch
) -> None:
    path, reason = _run_desktop_fallback(
        tmp_path=tmp_path, monkeypatch=monkeypatch, plant=b"<html>error</html>", timeout=1
    )

    assert path is None
    assert reason is None


def test_desktop_fallback_times_out_with_no_download(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    path, reason = _run_desktop_fallback(
        tmp_path=tmp_path, monkeypatch=monkeypatch, plant=None, timeout=1
    )

    assert path is None
    assert reason is None
    assert "Timed out waiting for a downloaded PDF" in capsys.readouterr().err
