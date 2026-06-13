from pathlib import Path

from pzi.pdf_planning import (
    build_browser_pdf_command,
    candidate_matches_requested_pdf_name,
    choose_firefox_profile,
    filename_match_text,
    is_pdf_content_type,
    needs_desktop_browser_fallback,
    normalized_hostname,
    parse_firefox_default_profile,
    requested_pdf_match_tokens,
    url_basename,
)


def test_is_pdf_content_type_classifies_pdf_html_and_ambiguous_values() -> None:
    assert is_pdf_content_type("application/pdf; charset=binary") is True
    assert is_pdf_content_type("text/html") is False
    assert is_pdf_content_type("application/json") is False
    assert is_pdf_content_type("text/plain") is False
    assert is_pdf_content_type("application/octet-stream") is None
    assert is_pdf_content_type(None) is None


def test_normalized_hostname_strips_www_and_rejects_invalid_url() -> None:
    assert normalized_hostname("https://www.biorxiv.org/content/10.1101/x") == "biorxiv.org"
    assert normalized_hostname("https://Example.COM/paper.pdf") == "example.com"
    assert normalized_hostname("http://[broken") is None


def test_needs_desktop_browser_fallback_for_known_preprint_hosts() -> None:
    assert needs_desktop_browser_fallback("https://www.biorxiv.org/content/x") is True
    assert needs_desktop_browser_fallback("https://medrxiv.org/content/x") is True
    assert needs_desktop_browser_fallback("https://example.org/paper.pdf") is False


def test_requested_pdf_match_tokens_include_citekey_basename_and_doi_tail() -> None:
    tokens = requested_pdf_match_tokens(
        url="https://example.org/files/deep-learning-2024.pdf",
        citekey="smith2024deep",
        record={"doi": "10.1101/2024.01.02.123456"},
    )

    assert tokens == {
        "smith2024deep",
        "deeplearning2024",
        "20240102123456",
    }


def test_candidate_matches_requested_pdf_name_by_strong_tokens_or_domain() -> None:
    assert candidate_matches_requested_pdf_name(
        filename="Smith2024Deep.pdf",
        url="https://example.org/files/other.pdf",
        citekey="smith2024deep",
    ) is True
    assert candidate_matches_requested_pdf_name(
        filename="example.pdf",
        url="https://example.org/files/other.pdf",
        citekey="smith2024deep",
    ) is True
    assert candidate_matches_requested_pdf_name(
        filename="unrelated.pdf",
        url="https://example.org/files/other.pdf",
        citekey="smith2024deep",
    ) is False


def test_filename_match_text_and_url_basename_are_pure_normalizers() -> None:
    assert filename_match_text(" Deep Learning 2024.PDF ") == "deeplearning2024"
    assert url_basename("https://example.org/a/b/Paper.pdf?download=1") == "Paper.pdf"
    assert url_basename("http://[broken") == ""

# ── from test_pdf_browser_plan.py ──


def test_build_browser_pdf_command_uses_explicit_command() -> None:
    assert (
        build_browser_pdf_command(
            env_cmd="custom hook",
            env_profile=None,
            env_browser="firefox",
            requested_browser=None,
            python_executable="python",
            firefox_profile=None,
            chrome_profile=None,
        )
        == "custom hook"
    )


def test_build_browser_pdf_command_uses_env_profile_and_browser() -> None:
    command = build_browser_pdf_command(
        env_cmd=None,
        env_profile="~/Browser Profile",
        env_browser="chrome beta",
        requested_browser=None,
        python_executable="/usr/bin/python3",
        firefox_profile=None,
        chrome_profile=None,
    )

    assert command == (
        "/usr/bin/python3 -m pzi.browser_pdf_hook "
        "--browser 'chrome beta' "
        f"--profile {str(Path('~/Browser Profile').expanduser())!r} "
        "--headful --challenge-timeout 120"
    )


def test_build_browser_pdf_command_prefers_requested_firefox_profile() -> None:
    command = build_browser_pdf_command(
        env_cmd=None,
        env_profile=None,
        env_browser="chrome",
        requested_browser="firefox",
        python_executable="python",
        firefox_profile=Path("/tmp/firefox profile"),
        chrome_profile=Path("/tmp/chrome"),
    )

    assert command == (
        "python -m pzi.browser_pdf_hook --browser firefox "
        "--profile '/tmp/firefox profile' --headful --challenge-timeout 120"
    )


def test_build_browser_pdf_command_falls_back_to_chrome_for_firefox() -> None:
    command = build_browser_pdf_command(
        env_cmd=None,
        env_profile=None,
        env_browser="firefox",
        requested_browser=None,
        python_executable="python",
        firefox_profile=None,
        chrome_profile=Path("/tmp/chrome"),
    )

    assert command == (
        "python -m pzi.browser_pdf_hook --browser chrome "
        "--profile /tmp/chrome --headful --challenge-timeout 120"
    )


def test_build_browser_pdf_command_uses_chromium_without_profiles() -> None:
    command = build_browser_pdf_command(
        env_cmd=None,
        env_profile=None,
        env_browser="firefox",
        requested_browser=None,
        python_executable="python",
        firefox_profile=None,
        chrome_profile=None,
    )

    assert command == (
        "python -m pzi.browser_pdf_hook --browser chromium "
        "--headful --challenge-timeout 120"
    )


def test_parse_firefox_default_profile_returns_relative_default() -> None:
    profiles_ini = """
[Profile0]
Name=default-release
IsRelative=1
Path=abc.default-release
Default=1
"""

    assert parse_firefox_default_profile(
        profiles_ini,
        base_dir=Path("/home/me/.mozilla/firefox"),
    ) == Path("/home/me/.mozilla/firefox/abc.default-release")


def test_parse_firefox_default_profile_returns_absolute_default() -> None:
    profiles_ini = """
[Profile0]
Name=custom
IsRelative=0
Path=/tmp/custom profile
Default=1
"""

    assert parse_firefox_default_profile(
        profiles_ini,
        base_dir=Path("/home/me/.mozilla/firefox"),
    ) == Path("/tmp/custom profile")


def test_choose_firefox_profile_prefers_existing_ini_default() -> None:
    selected = choose_firefox_profile(
        default_from_ini=Path("/tmp/default"),
        default_exists=lambda path: path == Path("/tmp/default"),
        profile_dirs=[Path("/tmp/new.default-release")],
        modified_time=lambda _path: 1.0,
    )

    assert selected == Path("/tmp/default")


def test_choose_firefox_profile_uses_newest_default_release() -> None:
    older = Path("/tmp/old.default-release")
    newer = Path("/tmp/new.default-release")

    selected = choose_firefox_profile(
        default_from_ini=None,
        default_exists=lambda _path: False,
        profile_dirs=[older, newer],
        modified_time=lambda path: 2.0 if path == newer else 1.0,
    )

    assert selected == newer


def test_choose_firefox_profile_uses_alphabetical_last_resort() -> None:
    selected = choose_firefox_profile(
        default_from_ini=None,
        default_exists=lambda _path: False,
        profile_dirs=[Path("/tmp/z.profile"), Path("/tmp/a.default")],
        modified_time=lambda _path: 1.0,
    )

    assert selected == Path("/tmp/a.default")
