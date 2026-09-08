"""`browser_profile_path` reaches the acquisition path, not just `pzi server`.

It was a documented config key (`config.template.toml`), parsed into the config
(`config.py`), and read by exactly one caller — the server's persistent browser
session. Every CLI acquisition path took the profile from `PZI_BROWSER_PROFILE`
or auto-detection and ignored the key, so setting it did nothing and said
nothing. That is this project's dominant defect shape — a value honoured at one
call site and not its siblings — applied to configuration.
"""

from __future__ import annotations

from pzi.pdf_planning import PdfFallbackSettings, browser_for_profile


def test_config_profile_is_used() -> None:
    settings = PdfFallbackSettings.from_config(
        {"browser_profile_path": "/home/t/.mozilla/firefox/abc.default-release"},
        env={},
    )
    assert settings.browser_profile == "/home/t/.mozilla/firefox/abc.default-release"


def test_config_profile_selects_the_browser_it_belongs_to() -> None:
    """No fourth browser key: the profile path already says which browser it is.
    `browser_engine`, `browser_pdf_cmd` and `PZI_BROWSER` are three settings
    that already overlap, and the docs apologise for it."""
    firefox = PdfFallbackSettings.from_config(
        {"browser_profile_path": "~/.mozilla/firefox/abc.default"}, env={}
    )
    chrome = PdfFallbackSettings.from_config(
        {"browser_profile_path": "~/.config/google-chrome"}, env={}
    )
    assert firefox.browser == "firefox"
    assert chrome.browser == "chrome"


def test_config_beats_the_environment() -> None:
    """Matching the rule already documented for `PZI_BROWSER_PDF_CMD` — the
    config wins, the environment is a fallback — rather than `PZI_NODE`'s
    opposite rule."""
    settings = PdfFallbackSettings.from_config(
        {"browser_profile_path": "/from/config/firefox"},
        env={"PZI_BROWSER_PROFILE": "/from/env"},
    )
    assert settings.browser_profile == "/from/config/firefox"


def test_environment_still_applies_when_the_config_is_silent() -> None:
    settings = PdfFallbackSettings.from_config(
        {}, env={"PZI_BROWSER_PROFILE": "/from/env", "PZI_BROWSER": "chrome"}
    )
    assert settings.browser_profile == "/from/env"
    assert settings.browser == "chrome"


def test_an_unrecognised_profile_does_not_guess_a_browser() -> None:
    settings = PdfFallbackSettings.from_config(
        {"browser_profile_path": "/opt/something/else"}, env={"PZI_BROWSER": "chrome"}
    )
    assert settings.browser == "chrome", "keep what was configured, do not guess"


def test_browser_for_profile_returns_none_when_it_cannot_tell() -> None:
    assert browser_for_profile(None) is None
    assert browser_for_profile("") is None
    assert browser_for_profile("/opt/whatever") is None


def test_the_pdf_command_path_carries_the_configured_profile() -> None:
    """The seam that was missing: `pdf retry` / `pdf attach` build their
    fallback kwargs here, and `settings` was never among them."""
    from pzi.pdf_service import _fallback_kwargs

    kwargs = _fallback_kwargs(
        {  # type: ignore[arg-type]
            "browser_profile_path": "~/.mozilla/firefox/abc.default",
            "api_listen_host": "127.0.0.1",
            "api_listen_port": 8765,
        }
    )
    settings = kwargs["settings"]
    assert settings.browser_profile == "~/.mozilla/firefox/abc.default"
    assert settings.browser == "firefox"
