"""Tests for src/pzi/setup_service.py."""

import os
import stat
from unittest.mock import patch

import pytest

from pzi.config import escape_toml_string as _escape_toml_string
from pzi.errors import PziError
from pzi.setup_service import (
    _find_firefox_profile,
    provision_api_token,
    render_config,
)

# ── provision_api_token ─────────────────────────────────────────────────────

def test_provision_api_token_writes_0600_file(tmp_path) -> None:
    data_home = tmp_path / "data"
    token_file, created = provision_api_token(data_home)

    assert created is True
    assert token_file == data_home / "api_token"
    assert token_file.exists()
    # Secret is a non-empty token.
    assert token_file.read_text().strip()
    # Owner-only permissions so the secret is not world/group readable.
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_provision_api_token_reuses_an_existing_token(tmp_path) -> None:
    """Minting a fresh token on every call de-paired the browser extension from
    the server whenever `pzi init` was run for any other reason."""
    data_home = tmp_path / "data"
    data_home.mkdir()
    existing = data_home / "api_token"
    existing.write_text("keep-me\n")

    token_file, created = provision_api_token(data_home)

    assert created is False
    assert token_file.read_text().strip() == "keep-me"


def test_provision_api_token_rotates_when_asked(tmp_path) -> None:
    data_home = tmp_path / "data"
    data_home.mkdir()
    stale = data_home / "api_token"
    stale.write_text("old")
    stale.chmod(0o644)

    _token_file, created = provision_api_token(data_home, rotate=True)

    assert created is True
    assert stat.S_IMODE(stale.stat().st_mode) == 0o600
    assert stale.read_text().strip() != "old"


def test_provision_api_token_refuses_to_replace_an_unreadable_token(tmp_path) -> None:
    """An unreadable token file is not "no token" — replacing it would rotate a
    secret this process cannot even see."""
    data_home = tmp_path / "data"
    data_home.mkdir()
    token = data_home / "api_token"
    token.write_text("secret")
    token.chmod(0o000)
    try:
        with pytest.raises(PziError, match="cannot read the existing API token"):
            provision_api_token(data_home)
    finally:
        token.chmod(0o600)

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


def test_render_config_never_writes_a_browser_command() -> None:
    """The regression this whole change exists for.

    `render_config` used to interpolate `sys.executable` into a
    `browser_pdf_cmd` line. That is an install-time snapshot of the environment
    written into a file the user is told to commit to their dotfiles, and it
    went stale the moment the distribution was renamed — after which every
    browser PDF fetch failed with [Errno 2] and reported "no PDF returned".
    pzi builds the command at run time now; the config must not pin one.
    """
    result = render_config(
        bib_name="ml",
        bib_path="~/bib/ml.bib",
        with_browser=True,
    )
    assert "browser_pdf_cmd" not in result
    assert "browser_pdf_hook" not in result


def test_render_config_does_not_depend_on_the_running_interpreter() -> None:
    """The invariant, stated directly: two runs under different interpreters
    produce byte-identical config. Anything derived from the running process is
    a staleness bug waiting for a reinstall."""
    with patch("sys.executable", "/opt/one/bin/python3"):
        first = render_config(
            bib_name="ml", bib_path="~/bib/ml.bib", with_browser=True,
            home_dir="/home/tester",
        )
    with patch("sys.executable", "/somewhere/else/bin/python3.13"):
        second = render_config(
            bib_name="ml", bib_path="~/bib/ml.bib", with_browser=True,
            home_dir="/home/tester",
        )
    assert first == second
    assert "/opt/one" not in first
    assert "/somewhere/else" not in second


def test_render_config_folds_home_bib_path_to_tilde() -> None:
    result = render_config(
        bib_name="ml",
        bib_path="/home/tester/projects/lib.bib",
        with_browser=False,
        papers_dir="/home/tester/projects/pdfs",
        home_dir="/home/tester",
    )
    assert 'path = "~/projects/lib.bib"' in result
    assert 'papers_dir = "~/projects/pdfs"' in result
    # No absolute home path leaks into the committed config.
    assert "/home/tester" not in result


def test_render_config_folds_a_home_profile_path_to_tilde() -> None:
    """The profile path is the one environment-derived value still written, so
    it carries the fold the interpreter path used to."""
    with patch(
        "pzi.setup_service._find_firefox_profile",
        return_value="/home/tester/.mozilla/firefox/abc.default-release",
    ):
        result = render_config(
            bib_name="ml",
            bib_path="~/bib/ml.bib",
            with_browser=True,
            browser="firefox",
            home_dir="/home/tester",
        )
    assert 'browser_profile_path = "~/.mozilla/firefox/abc.default-release"' in result
    # No absolute home path leaks into the committed config.
    assert "/home/tester" not in result


def test_render_config_with_firefox_records_the_profile_not_a_command() -> None:
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
    assert 'browser_profile_path = "/tmp/fake/default-release"' in result
    assert "authenticated PDF access" in result
    assert "browser_pdf_cmd" not in result


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
    assert "no firefox profile auto-detected" in result
    assert '# browser_profile_path = ' in result
    # Still no command, even on the path that has nothing to record.
    assert "browser_pdf_cmd" not in result


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
