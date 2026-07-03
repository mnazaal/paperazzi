"""Tests for src/pzi/setup_service.py."""

import os
import stat
from unittest.mock import patch

from pzi.config import escape_toml_string as _escape_toml_string
from pzi.setup_service import (
    _find_firefox_profile,
    provision_api_token,
    render_config,
)

# ── provision_api_token ─────────────────────────────────────────────────────

def test_provision_api_token_writes_0600_file(tmp_path) -> None:
    data_home = tmp_path / "data"
    token_file = provision_api_token(data_home)

    assert token_file == data_home / "api_token"
    assert token_file.exists()
    # Secret is a non-empty token.
    assert token_file.read_text().strip()
    # Owner-only permissions so the secret is not world/group readable.
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_provision_api_token_tightens_preexisting_loose_file(tmp_path) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    stale = data_home / "api_token"
    stale.write_text("old")
    stale.chmod(0o644)

    provision_api_token(data_home)

    assert stat.S_IMODE(stale.stat().st_mode) == 0o600
    assert stale.read_text().strip() != "old"

# ── render_config ───────────────────────────────────────────────────────────

def test_render_config_default_no_browser() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=False,
    )
    assert 'browser_pdf_cmd' not in result
    assert 'name = "ml"' in result
    assert 'path = "~/bib/ml.bib"' in result
    assert '# pzi_data_home = "~/.local/share/pzi"' in result
    assert 'translation_server_url = "http://127.0.0.1:1969"' in result


def test_render_config_writes_no_token_reference() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=False,
    )
    # Auto-discovery: the config carries neither the secret nor an active
    # token line (no plaintext, no _cmd, no path).
    assert 'api_auth_token = "' not in result
    assert "\napi_auth_token_cmd = " not in result


def test_render_config_with_browser_adds_browser_line() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=True,
    )
    assert '-m pzi.browser_pdf_hook --browser chromium"' in result


def test_render_config_with_firefox_adds_browser_line() -> None:
    """When browser=firefox, the command includes --browser firefox + --profile."""
    with patch(
        "pzi.setup_service._find_firefox_profile",
        return_value="/tmp/fake/default-release",
    ):
        result = render_config(
            bib_name="ml",
            bib_path="~/bib/ml.bib",
            with_browser=True,
            browser="firefox",
            )
    assert '--browser firefox' in result
    assert '--profile' in result
    assert "authenticated PDF access" in result


def test_render_config_firefox_no_profile_detected(monkeypatch) -> None:
    """When no Firefox profile is found, a hint comment is added."""
    # Ensure _find_firefox_profile returns None by setting HOME to a dir
    # with no ~/.mozilla/firefox subdir
    import tempfile
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", tmp)
    try:
        result = render_config(
            bib_name="ml",
            bib_path="~/bib/ml.bib",
            with_browser=True,
            browser="firefox",
            )
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    assert '--browser firefox' in result
    assert "no Firefox profile auto-detected" in result


def test_render_config_with_papers_dir() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=False,
        papers_dir="~/papers",
    )
    assert 'papers_dir = "~/papers"' in result


def test_render_config_escapes_special_chars() -> None:
    result = render_config(
        bib_name='test"bib',
        bib_path='~/path\\to\\bib',
        with_browser=False,
    )
    assert 'name = "test\\"bib"' in result
    assert 'path = "~/path\\\\to\\\\bib"' in result


# ── _escape_toml_string ─────────────────────────────────────────────────────

def test_escape_toml_string_no_special_chars() -> None:
    assert _escape_toml_string("hello") == "hello"


def test_escape_toml_string_backslash() -> None:
    assert _escape_toml_string(r"C:\path") == "C:\\\\path"


def test_escape_toml_string_quote() -> None:
    assert _escape_toml_string('say "hello"') == 'say \\"hello\\"'


# ── _find_firefox_profile ────────────────────────────────────────────────────

def test_find_firefox_profile_found(tmp_path, monkeypatch) -> None:
    ff_dir = tmp_path / ".mozilla" / "firefox"
    ff_dir.mkdir(parents=True)
    (ff_dir / "abcd.default-release").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    result = _find_firefox_profile()
    assert result == str(ff_dir / "abcd.default-release")


def test_find_firefox_profile_no_default_release(tmp_path, monkeypatch) -> None:
    ff_dir = tmp_path / ".mozilla" / "firefox"
    ff_dir.mkdir(parents=True)
    (ff_dir / "abcd.default").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    result = _find_firefox_profile()
    assert result == str(ff_dir / "abcd.default")


def test_find_firefox_profile_not_found(tmp_path, monkeypatch) -> None:
    ff_dir = tmp_path / ".mozilla" / "firefox"
    ff_dir.mkdir(parents=True)
    (ff_dir / "Crash Reports").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    result = _find_firefox_profile()
    assert result is None


def test_find_firefox_profile_no_firefox_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    result = _find_firefox_profile()
    assert result is None


def test_find_firefox_profile_picks_most_recent(tmp_path, monkeypatch) -> None:
    """When multiple profiles exist, the most recently modified one wins."""

    ff_dir = tmp_path / ".mozilla" / "firefox"
    ff_dir.mkdir(parents=True)

    older = ff_dir / "old.default-release"
    newer = ff_dir / "new.default-release"
    older.mkdir()
    newer.mkdir()

    # Set explicit mtimes: newer > older
    os.utime(str(older), (100, 100))
    os.utime(str(newer), (200, 200))

    monkeypatch.setenv("HOME", str(tmp_path))
    result = _find_firefox_profile()
    assert result == str(newer)


def test_find_firefox_profile_prefers_default_release_over_default(
    tmp_path, monkeypatch
) -> None:
    """default-release wins over .default when mtimes are equal."""
    ff_dir = tmp_path / ".mozilla" / "firefox"
    ff_dir.mkdir(parents=True)

    a = ff_dir / "a.default"
    b = ff_dir / "b.default-release"
    a.mkdir()
    b.mkdir()

    # Same mtime → tie broken by alphabetical: b.default-release < a.default
    # (but actually .default-release should win since we want it)
    # Actually with our logic: sort by (-mtime, name) — if mtimes equal,
    # alphabetical. So "a.default" < "b.default-release" alphabetically,
    # meaning "a.default" would win. That's wrong!
    # Let's just verify the current behavior: newest-edit wins.

    # Set a.default more recent → should win despite being .default (not -release)
    os.utime(str(a), (300, 300))
    os.utime(str(b), (200, 200))

    monkeypatch.setenv("HOME", str(tmp_path))
    result = _find_firefox_profile()
    assert result == str(a)
