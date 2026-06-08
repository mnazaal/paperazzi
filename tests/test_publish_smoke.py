import subprocess
from pathlib import Path

from tools import publish_smoke


def test_env_status_masks_secret_values() -> None:
    env = {
        "PZI_CONTACT_EMAIL": "person@example.com",
        "PZI_UNPAYWALL_EMAIL": "person@example.com",
        "PZI_S2_API_KEY": "secret-key",
    }

    status = publish_smoke.masked_env_status(env)

    assert status == {
        "PZI_CONTACT_EMAIL": "set",
        "PZI_UNPAYWALL_EMAIL": "set",
        "PZI_S2_API_KEY": "set",
        "PZI_S2_API_KEY_CMD": "unset",
        "PZI_CHROMIUM_PROFILE": "unset",
        "PZI_FIREFOX_PROFILE": "unset",
    }
    assert "secret-key" not in repr(status)
    assert "person@example.com" not in repr(status)


def test_browser_profile_map_uses_browser_specific_env_vars() -> None:
    profiles = publish_smoke.browser_profiles(
        {
            "PZI_CHROMIUM_PROFILE": "~/chromium-profile",
            "PZI_FIREFOX_PROFILE": "~/firefox-profile",
            "PZI_BROWSER": "firefox",
        }
    )

    assert profiles == {
        "chromium": "~/chromium-profile",
        "firefox": "~/firefox-profile",
    }


def test_browser_pdf_cmd_for_browser_requires_matching_profile() -> None:
    profiles = {"chromium": "/home/me/chromium", "firefox": ""}

    assert publish_smoke.browser_pdf_cmd_for("chromium", profiles) == (
        "pzi-browser-hook --profile /home/me/chromium --browser chromium"
    )
    assert publish_smoke.browser_pdf_cmd_for("firefox", profiles) is None


def test_render_result_table_includes_browser_and_skip_reason() -> None:
    rows = [
        publish_smoke.SmokeResult(
            case="nature",
            browser="firefox",
            metadata="skip",
            pdf="skip",
            status="skip",
            reason="PZI_FIREFOX_PROFILE unset",
        )
    ]

    table = publish_smoke.render_result_table(rows)

    assert "case" in table
    assert "browser" in table
    assert "nature" in table
    assert "firefox" in table
    assert "PZI_FIREFOX_PROFILE unset" in table


def test_envrc_git_guard_allows_ignored_untracked_envrc(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".envrc\n", encoding="utf-8")

    result = publish_smoke.envrc_guard(tmp_path, runner=_fake_git_runner(set()))

    assert result is None


def test_envrc_git_guard_rejects_tracked_envrc(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".envrc\n", encoding="utf-8")

    result = publish_smoke.envrc_guard(tmp_path, runner=_fake_git_runner({".envrc"}))

    assert result == ".envrc is tracked by git; remove secrets from git before smoke tests"


def test_envrc_git_guard_requires_ignore_entry(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")

    result = publish_smoke.envrc_guard(tmp_path, runner=_fake_git_runner(set()))
    assert result == ".envrc is not ignored; add it to .gitignore before storing secrets"


def _fake_git_runner(tracked: set[str]):
    def run(args, **_kwargs):
        assert args == ["git", "ls-files", ".envrc"]
        stdout = ".envrc\n" if ".envrc" in tracked else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return run
