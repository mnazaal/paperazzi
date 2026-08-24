from pathlib import Path

import pytest

from pzi import config as config_module
from pzi.config import (
    default_config_path,
    default_data_home,
    derive_papers_dir,
    resolve_bib,
    tildify_path,
    validate_app_config,
    validate_bib_config,
    xdg_config_home,
    xdg_data_home,
)

HOME = "/home/tester"


def test_tildify_path_folds_home_prefix() -> None:
    assert tildify_path("/home/tester/bib/ml.bib", home_dir=HOME) == "~/bib/ml.bib"


def test_tildify_path_returns_bare_tilde_for_home_itself() -> None:
    assert tildify_path("/home/tester", home_dir=HOME) == "~"


def test_tildify_path_leaves_non_home_paths_unchanged() -> None:
    assert tildify_path("/usr/bin/python3", home_dir=HOME) == "/usr/bin/python3"


def test_tildify_path_round_trips_through_normalize() -> None:
    original = "/home/tester/projects/lib.bib"
    folded = tildify_path(original, home_dir=HOME)
    config, errors = validate_bib_config(
        {"name": "ml", "path": folded}, home_dir=HOME
    )
    assert errors == []
    assert config["path"] == original


def test_validate_bib_config_derives_default_papers_dir() -> None:
    config, errors = validate_bib_config(
        {
            "name": "ml",
            "path": "~/bib/ml.bib",
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config == {
        "name": "ml",
        "path": "/home/tester/bib/ml.bib",
        "papers_dir": "/home/tester/bib/papers",
        "default": False,
    }


def test_validate_bib_config_uses_explicit_papers_dir() -> None:
    config, errors = validate_bib_config(
        {
            "name": "ml",
            "path": "~/bib/ml.bib",
            "papers_dir": "~/papers/ml",
            "default": True,
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config == {
        "name": "ml",
        "path": "/home/tester/bib/ml.bib",
        "papers_dir": "/home/tester/papers/ml",
        "default": True,
    }


def test_validate_bib_config_rejects_invalid_fields() -> None:
    config, errors = validate_bib_config(
        {
            "name": "",
            "path": 42,
            "papers_dir": 1,
            "default": "yes",
        },
        home_dir=HOME,
    )

    assert config is None
    assert errors == [
        "bib.name must be a non-empty string",
        "bib.path must be a non-empty string",
        "bib.papers_dir must be a string when provided",
        "bib.default must be a boolean",
    ]


def test_default_translation_server_url_literal(real_translation_server_url) -> None:
    """Pin the shipped default, which the suite otherwise patches away.

    `_dead_default_translation_server` in conftest repoints the constant so a
    config omitting the key cannot reach a live server. That protection would
    also hide an accidental change to the shipped default, so the pristine
    value is captured at conftest import and asserted here: 1969 is
    translation-server's own port and the value `config.template.toml` shows.
    """
    assert real_translation_server_url == "http://127.0.0.1:1969"


def test_validate_app_config_applies_defaults() -> None:
    config, errors = validate_app_config(
        {
            "bibs": [
                {
                    "name": "ml",
                    "path": "~/bib/ml.bib",
                }
            ]
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config == {
        # Read from the constant, not spelled out: the suite repoints this
        # default at a dead port so no test can reach a real translation
        # server by omitting the key. What this assertion pins is that the
        # default is *applied*; `test_default_translation_server_url_literal`
        # pins what the default actually is.
        "translation_server_url": config_module.DEFAULT_TRANSLATION_SERVER_URL,
        "bibs": [
            {
                "name": "ml",
                "path": "/home/tester/bib/ml.bib",
                "papers_dir": "/home/tester/bib/papers",
                "default": False,
            }
        ],
        "api_listen_host": "127.0.0.1",
        "api_listen_port": 8765,
        "api_auth_token": None,
        "api_auth_token_cmd": None,
        "api_allowed_origins": None,
        "capture_source_dirs": (),
        "inbox_path": None,
        "api_max_body_bytes": 67108864,
        "contact_email": None,
        "contact_email_cmd": None,
        "unpaywall_email": None,
        "unpaywall_email_cmd": None,
        "semantic_scholar_api_key": None,
        "semantic_scholar_api_key_cmd": None,
        "flaresolverr_url": None,
        "browser_pdf_cmd": None,
        "citekey_format": None,
        "pdf_filename_format": None,
        "pdf_file_path_style": "absolute",
        "page_metadata_cmd": None,
        "page_metadata_timeout_seconds": 5,
        "metadata_confidence_min_score": 0,
        "promote_confidence_threshold": 60,
        "promote_recheck_after_days": 30,
        "metadata_cache_ttl": 0,
        "browser_hook": True,
        "pzi_data_home": f"{HOME}/.local/share/pzi",
        "node_path": None,
        "api_url": "http://127.0.0.1:8765",
        "browser_profile_path": None,
        "browser_engine": "chromium",
        "pdf_discovery_parallel": False,
            "desktop_fallback_hosts": ["biorxiv.org", "medrxiv.org", "researchsquare.com", "ssrn.com", "authorea.com"],
            "ezproxy_host": None,
        }


def test_validate_app_config_accepts_browser_pdf_cmd() -> None:
    config, errors = validate_app_config(
        {
            "browser_pdf_cmd": "python /tmp/browser_hook.py",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["browser_pdf_cmd"] == "python /tmp/browser_hook.py"


def test_validate_app_config_accepts_relative_pdf_file_path_style() -> None:
    config, errors = validate_app_config(
        {
            "pdf_file_path_style": "relative",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["pdf_file_path_style"] == "relative"


def test_validate_app_config_accepts_page_metadata_cmd() -> None:
    config, errors = validate_app_config(
        {
            "page_metadata_cmd": "paper-metadata --json",
            "page_metadata_timeout_seconds": 9,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["page_metadata_cmd"] == "paper-metadata --json"
    assert config["page_metadata_timeout_seconds"] == 9


def test_validate_app_config_rejects_duplicate_names_and_multiple_defaults() -> None:
    config, errors = validate_app_config(
        {
            "bibs": [
                {"name": "ml", "path": "~/bib/ml.bib", "default": True},
                {"name": "ml", "path": "~/bib/other.bib", "default": True},
            ]
        },
        home_dir=HOME,
    )

    assert config is None
    assert errors == [
        "duplicate bib name: ml",
        "at most one bib may be marked as default",
    ]


def test_validate_app_config_accepts_unpaywall_email_cmd() -> None:
    config, errors = validate_app_config(
        {
            "unpaywall_email_cmd": "pass show unpaywall-email",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["unpaywall_email_cmd"] == "pass show unpaywall-email"
    assert config["unpaywall_email"] is None


def test_validate_app_config_rejects_invalid_top_level_fields() -> None:
    config, errors = validate_app_config(
        {
            "translation_server_url": "ftp://example.com",
            "api_listen_host": "",
            "api_listen_port": 70000,
            "api_auth_token": 7,
            "api_allowed_origins": "*",
            "api_max_body_bytes": -1,
            "bibs": [],
        },
        home_dir=HOME,
    )

    assert config is None
    assert errors == [
        "translation_server_url must be an http or https URL",
        "api_listen_host must be a non-empty string",
        "api_listen_port must be an integer between 1 and 65535",
        "api_auth_token must be a string when provided",
        "api_allowed_origins must be a list of strings when provided",
        "api_max_body_bytes must be a non-negative integer",
        "bibs must be a non-empty list",
    ]


def test_validate_app_config_accepts_api_security_fields() -> None:
    config, errors = validate_app_config(
        {
            "api_auth_token": " secret ",
            "api_allowed_origins": [" http://127.0.0.1 ", ""],
            "api_max_body_bytes": 1024,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["api_auth_token"] == "secret"
    assert config["api_allowed_origins"] == ("http://127.0.0.1",)
    assert config["api_max_body_bytes"] == 1024


def test_resolve_bib_by_default_when_single_bib() -> None:
    bib = {
        "name": "ml",
        "path": "/home/tester/bib/ml.bib",
        "papers_dir": "/home/tester/bib/papers",
        "default": False,
    }

    assert resolve_bib([bib]) == bib


def test_resolve_bib_returns_none_when_ambiguous_without_default() -> None:
    bibs = [
        {
            "name": "ml",
            "path": "/home/tester/bib/ml.bib",
            "papers_dir": "/home/tester/bib/papers",
            "default": False,
        },
        {
            "name": "systems",
            "path": "/home/tester/bib/systems.bib",
            "papers_dir": "/home/tester/bib/papers",
            "default": False,
        },
    ]

    assert resolve_bib(bibs) is None


def test_derive_papers_dir_returns_sibling_directory() -> None:
    assert derive_papers_dir("/home/tester/bib/ml.bib") == "/home/tester/bib/papers"


# ── XDG base-directory resolution ───────────────────────────────────────────
# (conftest's _clear_xdg_env unsets XDG_* by default, so unset-fallback is the
#  baseline; tests that want the set case re-set the vars explicitly.)

def test_xdg_dirs_fall_back_to_home_when_unset() -> None:
    assert xdg_config_home(HOME) == f"{HOME}/.config"
    assert xdg_data_home(HOME) == f"{HOME}/.local/share"
    assert default_config_path(HOME) == f"{HOME}/.config/pzi/config.toml"
    assert default_data_home(HOME) == f"{HOME}/.local/share/pzi"


def test_xdg_dirs_honor_absolute_env(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg/cfg")
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    assert default_config_path(HOME) == "/xdg/cfg/pzi/config.toml"
    assert default_data_home(HOME) == "/xdg/data/pzi"


def test_xdg_dirs_ignore_relative_env(monkeypatch) -> None:
    # The XDG spec mandates ignoring non-absolute values.
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
    monkeypatch.setenv("XDG_DATA_HOME", "also/relative")
    assert default_config_path(HOME) == f"{HOME}/.config/pzi/config.toml"
    assert default_data_home(HOME) == f"{HOME}/.local/share/pzi"


def test_app_config_data_home_honors_xdg(monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    config, errors = validate_app_config(
        {"bibs": [{"name": "ml", "path": "~/bib/ml.bib"}]},
        home_dir=HOME,
    )
    assert errors == []
    assert config["pzi_data_home"] == "/xdg/data/pzi"


def test_app_config_explicit_data_home_overrides_xdg(monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    config, errors = validate_app_config(
        {
            "pzi_data_home": "/explicit/dir",
            "bibs": [{"name": "ml", "path": "~/bib/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert errors == []
    assert config["pzi_data_home"] == "/explicit/dir"


def test_validate_app_config_rejects_an_unparseable_pdf_filename_format() -> None:
    """A one-character typo used to traceback out of every add/pdf.

    The renderer now degrades instead of raising, so without this check the
    typo would silently drop the option from every filename it touches.
    """
    config, errors = validate_app_config(
        {
            "pdf_filename_format": '{{ title truncate="100 }}',
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert config is None
    assert any("pdf_filename_format is not a valid template" in e for e in errors)


def test_validate_app_config_rejects_an_unparseable_citekey_format() -> None:
    config, errors = validate_app_config(
        {
            "citekey_format": "{{ auth suffix='x }}",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert config is None
    assert any("citekey_format is not a valid template" in e for e in errors)


def test_validate_app_config_accepts_the_documented_template_default() -> None:
    """The commented-out suggestion in config.template.toml must validate."""
    config, errors = validate_app_config(
        {
            "pdf_filename_format": (
                '{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}'
                '{{ title truncate="100" }}'
            ),
            "citekey_format": "auth.lower + shorttitle(3,3) + year",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None


def test_validate_app_config_rejects_wrong_types_for_optional_string_keys() -> None:
    """A wrong type used to read as "unset" and vanish.

    `_opt_str_from_raw` returns None for any non-string, so `node_path = 22`
    took exactly the silent fallback the template, README and CHANGELOG all
    disclaim ("a set-but-broken value is a hard error, not a silent fallback").
    """
    for key in (
        "flaresolverr_url",
        "api_url",
        "browser_pdf_cmd",
        "node_path",
        "browser_profile_path",
    ):
        config, errors = validate_app_config(
            {key: 22, "bibs": [{"name": "ml", "path": "~/ml.bib"}]}, home_dir=HOME
        )
        assert config is None, key
        assert f"{key} must be a string when provided" in errors, key


def test_validate_app_config_rejects_a_schemeless_flaresolverr_url() -> None:
    """It was nulled by the normalizer with no error, so the feature went off
    silently — and `add_planning` then told the user to configure it."""
    config, errors = validate_app_config(
        {
            "flaresolverr_url": "127.0.0.1:8191",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert config is None
    assert "flaresolverr_url must be an http or https URL" in errors


def test_validate_app_config_still_accepts_a_well_formed_flaresolverr_url() -> None:
    config, errors = validate_app_config(
        {
            "flaresolverr_url": "http://127.0.0.1:8191",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["flaresolverr_url"] == "http://127.0.0.1:8191"


def test_explicit_empty_api_allowed_origins_means_none(tmp_path) -> None:
    """`[]` used to revert to the permissive defaults at two separate layers.

    A user writing this means "allow no cross-origin requests"; they silently
    got `chrome-extension://`, `moz-extension://` and localhost instead.
    """
    from pzi.http_security import build_http_security_config

    config, errors = validate_app_config(
        {"api_allowed_origins": [], "bibs": [{"name": "ml", "path": "~/ml.bib"}]},
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["api_allowed_origins"] == ()
    # ...and it survives the second `or` in the consumer.
    security = build_http_security_config(allowed_origins=config["api_allowed_origins"])
    assert security["allowed_origins"] == ()


def test_absent_api_allowed_origins_still_means_defaults() -> None:
    from pzi.http_security import DEFAULT_ALLOWED_ORIGINS, build_http_security_config

    config, errors = validate_app_config(
        {"bibs": [{"name": "ml", "path": "~/ml.bib"}]}, home_dir=HOME
    )

    assert errors == []
    assert config is not None
    assert config["api_allowed_origins"] is None
    security = build_http_security_config(allowed_origins=config["api_allowed_origins"])
    assert security["allowed_origins"] == DEFAULT_ALLOWED_ORIGINS


def test_explicit_empty_desktop_fallback_hosts_means_none() -> None:
    """Same shape, and the default was re-expanded at three layers here."""
    from pzi.pdf_planning import needs_desktop_browser_fallback

    config, errors = validate_app_config(
        {"desktop_fallback_hosts": [], "bibs": [{"name": "ml", "path": "~/ml.bib"}]},
        home_dir=HOME,
    )

    assert errors == []
    assert config is not None
    assert config["desktop_fallback_hosts"] == []
    # biorxiv is in the default set, so this is the discriminating case.
    assert not needs_desktop_browser_fallback(
        "https://www.biorxiv.org/content/10.1101/x.pdf",
        hosts=set(config["desktop_fallback_hosts"]),
    )


def test_absent_desktop_fallback_hosts_still_means_defaults() -> None:
    from pzi.pdf_planning import needs_desktop_browser_fallback

    config, errors = validate_app_config(
        {"bibs": [{"name": "ml", "path": "~/ml.bib"}]}, home_dir=HOME
    )

    assert errors == []
    assert config is not None
    assert "biorxiv.org" in config["desktop_fallback_hosts"]
    assert needs_desktop_browser_fallback(
        "https://www.biorxiv.org/content/10.1101/x.pdf",
        hosts=set(config["desktop_fallback_hosts"]),
    )


def test_a_relative_bib_path_resolves_against_the_config_file(tmp_path, monkeypatch) -> None:
    """It resolved against the *current directory*, so `path = "ml.bib"` named a
    different file depending on where `pzi` happened to be run from — and a
    config that worked from the project root silently created a second, empty
    library elsewhere."""
    from pzi.config import load_config_file

    project = tmp_path / "project"
    project.mkdir()
    (project / "ml.bib").write_text("", encoding="utf-8")
    config_path = project / "config.toml"
    config_path.write_text(
        '[[bibs]]\nname = "ml"\npath = "ml.bib"\ndefault = true\n', encoding="utf-8"
    )

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = load_config_file(str(config_path), home_dir=str(tmp_path))

    assert result["errors"] == []
    assert result["config"]["bibs"][0]["path"] == str(project / "ml.bib")


def test_a_relative_papers_dir_resolves_against_the_config_file(tmp_path, monkeypatch) -> None:
    from pzi.config import load_config_file

    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "config.toml"
    config_path.write_text(
        '[[bibs]]\nname = "ml"\npath = "ml.bib"\npapers_dir = "pdfs"\ndefault = true\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = load_config_file(str(config_path), home_dir=str(tmp_path))

    assert result["config"]["bibs"][0]["papers_dir"] == str(project / "pdfs")


def test_an_absolute_or_home_relative_bib_path_is_unaffected(tmp_path, monkeypatch) -> None:
    from pzi.config import load_config_file

    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "abs"\npath = "{tmp_path / "a.bib"}"\ndefault = true\n\n'
        '[[bibs]]\nname = "home"\npath = "~/h.bib"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    bibs = load_config_file(str(config_path), home_dir=str(tmp_path))["config"]["bibs"]

    assert bibs[0]["path"] == str(tmp_path / "a.bib")
    assert bibs[1]["path"] == str(tmp_path / "h.bib")


def test_a_retired_config_key_still_loads_and_says_why(tmp_path) -> None:
    """`rate_limit_rpm` no longer exists, and an existing config must not break.

    It falls through the unknown-key path, which is a *warning* — so the config
    loads — but "unknown config key" reads as a typo when the real answer is
    that the inbound rate limiter was removed.
    """
    from pzi.config import load_config_file

    bib = tmp_path / "ml.bib"
    bib.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'rate_limit_rpm = 120\n\n[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n',
        encoding="utf-8",
    )

    result = load_config_file(str(config_path), home_dir=str(tmp_path))

    assert result["config"] is not None, result["errors"]
    assert not result["errors"]
    warning = "\n".join(result.get("warnings") or [])
    assert "rate_limit_rpm" in warning
    assert "retired" in warning
    assert "unknown config key" not in warning


def test_a_typo_inside_a_bibs_table_is_reported(tmp_path: Path) -> None:
    """Only top-level keys were checked.

    `papers_dirs` or `defualt` in a `[[bibs]]` table was accepted with no
    warning and no error — and those two settings decide where every PDF is
    written and which library a command acts on.
    """
    from pzi.config import unknown_config_keys

    warnings = unknown_config_keys(
        {
            "contact_email": "a@b.c",
            "bibs": [
                {"name": "ml", "path": "/x.bib", "papers_dirs": "/p", "defualt": True},
            ],
        }
    )

    assert any("papers_dirs" in w for w in warnings), warnings
    assert any("defualt" in w for w in warnings), warnings
    # The library is named, so the user knows which table to fix.
    assert all("'ml'" in w for w in warnings), warnings


def test_a_symlinked_target_resolves_to_the_configured_library(tmp_path: Path) -> None:
    """The write layer resolves symlinks and target resolution did not.

    So `--target` via a symlink missed the configured entry and fell through to
    the ad-hoc branch: the lock and the replace landed on the right file while
    `papers_dir` came from the symlink's own directory, splitting one library's
    PDFs across two places.
    """
    from pzi.config import resolve_library_target

    real = tmp_path / "real.bib"
    real.write_text("")
    papers = tmp_path / "papers"
    papers.mkdir()
    link = tmp_path / "link.bib"
    link.symlink_to(real)

    bibs = [
        {"name": "ml", "path": str(real), "papers_dir": str(papers), "default": True}
    ]
    resolved = resolve_library_target(bibs, str(link), home_dir=str(tmp_path))

    assert resolved is not None
    assert resolved["name"] == "ml"
    assert resolved["papers_dir"] == str(papers)


def test_a_directory_is_not_a_library(tmp_path: Path) -> None:
    """Existence was the only check, so a directory named `refs.bib` resolved
    and every command then failed inside the reader naming no target."""
    from pzi.config import resolve_library_target

    directory = tmp_path / "refs.bib"
    directory.mkdir()

    assert resolve_library_target([], str(directory), home_dir=str(tmp_path)) is None


# === one bad value per key, and one gate (2026-08-23 audit, item 539) ===

#: One rejectable value per config key, with the message the user is owed.
#: Derived-from-source completeness is asserted below, so a key added to
#: `AppConfig` without a validation rule fails here rather than loading
#: whatever the user wrote. 24 of these `errors.append` lines had never been
#: executed by a test.
_BAD_VALUES: list[tuple[str, object, str]] = [
    ("api_allowed_origins", "*", "api_allowed_origins must be a list of strings when provided"),
    ("api_auth_token", 7, "api_auth_token must be a string when provided"),
    ("api_auth_token_cmd", 7, "api_auth_token_cmd must be a string when provided"),
    ("api_listen_host", "", "api_listen_host must be a non-empty string"),
    ("api_listen_port", 70000, "api_listen_port must be an integer between 1 and 65535"),
    ("api_max_body_bytes", -1, "api_max_body_bytes must be a non-negative integer"),
    ("api_url", "ftp://example.com", "api_url must be an http or https URL"),
    ("bibs", [], "bibs must be a non-empty list"),
    ("browser_engine", "opera", "browser_engine must be 'chromium', 'firefox', or 'webkit'"),
    ("browser_hook", "yes", "browser_hook must be a boolean"),
    ("browser_pdf_cmd", 7, "browser_pdf_cmd must be a string when provided"),
    ("browser_profile_path", 7, "browser_profile_path must be a string when provided"),
    (
        "capture_source_dirs",
        ["/tmp", 7],
        "capture_source_dirs must be a list of strings when provided",
    ),
    (
        "citekey_format",
        '{{ author truncate="8 }}',
        "citekey_format is not a valid template",
    ),
    ("contact_email", 7, "contact_email must be a string when provided"),
    ("contact_email_cmd", 7, "contact_email_cmd must be a string when provided"),
    (
        "desktop_fallback_hosts",
        "example.com",
        "desktop_fallback_hosts must be a list when provided",
    ),
    ("ezproxy_host", "https://proxy.example.edu", "ezproxy_host must be a bare hostname"),
    ("flaresolverr_url", "ftp://example.com", "flaresolverr_url must be an http or https URL"),
    ("inbox_path", 7, "inbox_path must be a string when provided"),
    ("metadata_cache_ttl", -1, "metadata_cache_ttl must be a non-negative integer"),
    (
        "metadata_confidence_min_score",
        1000,
        "metadata_confidence_min_score must be between 0 and 100 (got 1000)",
    ),
    ("node_path", 7, "node_path must be a string when provided"),
    ("page_metadata_cmd", 7, "page_metadata_cmd must be a string when provided"),
    (
        "page_metadata_timeout_seconds",
        0,
        "page_metadata_timeout_seconds must be a positive integer",
    ),
    ("pdf_discovery_parallel", "yes", "pdf_discovery_parallel must be a boolean"),
    ("pdf_file_path_style", "somewhere", "pdf_file_path_style must be 'absolute' or 'relative'"),
    (
        "pdf_filename_format",
        '{{ title truncate="100 }}',
        "pdf_filename_format is not a valid template",
    ),
    (
        "promote_confidence_threshold",
        101,
        "promote_confidence_threshold must be an integer between 0 and 100",
    ),
    (
        "promote_recheck_after_days",
        -1,
        "promote_recheck_after_days must be a non-negative integer",
    ),
    ("pzi_data_home", "   ", "pzi_data_home must be a non-empty string"),
    ("semantic_scholar_api_key", 7, "semantic_scholar_api_key must be a string when provided"),
    (
        "semantic_scholar_api_key_cmd",
        7,
        "semantic_scholar_api_key_cmd must be a string when provided",
    ),
    (
        "translation_server_url",
        "ftp://example.com",
        "translation_server_url must be an http or https URL",
    ),
    ("unpaywall_email", 7, "unpaywall_email must be a string when provided"),
    ("unpaywall_email_cmd", 7, "unpaywall_email_cmd must be a string when provided"),
]


def _valid_raw() -> dict[str, object]:
    return {"bibs": [{"name": "ml", "path": "~/ml.bib"}]}


def test_every_config_key_has_a_rejectable_value_in_the_table() -> None:
    """The table is exhaustive by construction, not by memory.

    A key added to `AppConfig` with no row here is either unvalidated — the
    defect this item is about — or validated with nothing exercising it.
    """
    covered = {key for key, _value, _message in _BAD_VALUES}
    declared = set(config_module.AppConfig.__annotations__)
    assert covered == declared, (
        f"config keys with no rejection test: {sorted(declared - covered)}; "
        f"rows naming no config key: {sorted(covered - declared)}"
    )


@pytest.mark.parametrize(
    "key,value,message", _BAD_VALUES, ids=[key for key, _v, _m in _BAD_VALUES]
)
def test_a_bad_value_is_rejected_and_named(key: str, value: object, message: str) -> None:
    config, errors = validate_app_config({**_valid_raw(), key: value}, home_dir=HOME)

    assert config is None, f"{key}={value!r} loaded"
    assert any(message in error for error in errors), (
        f"{key}={value!r} was rejected without saying why: {errors}"
    )


def test_unrelated_faults_are_all_reported_in_one_pass() -> None:
    """Four keys, one from each of the validator's four former staged gates.

    The gates returned at the first non-empty `errors`, so a user with faults in
    two of them fixed one, re-ran, and was told about the next — with no way to
    tell how many rounds were left. Which tier a key landed in was an accident
    of where its check was written.
    """
    config, errors = validate_app_config(
        {
            **_valid_raw(),
            "api_listen_host": "",  # gate 1
            "contact_email": 7,  # gate 2
            "browser_hook": "yes",  # gate 3
            "pzi_data_home": "   ",  # gate 4
        },
        home_dir=HOME,
    )

    assert config is None
    assert errors == [
        "api_listen_host must be a non-empty string",
        "contact_email must be a string when provided",
        "browser_hook must be a boolean",
        "pzi_data_home must be a non-empty string",
    ]


def test_a_bad_bib_entry_does_not_hide_a_bad_top_level_key() -> None:
    """The bib list used to *replace* the error list, not extend it."""
    config, errors = validate_app_config(
        {"api_listen_host": "", "bibs": [{"name": "ml"}]}, home_dir=HOME
    )

    assert config is None
    assert "api_listen_host must be a non-empty string" in errors
    assert any("path" in error for error in errors), errors


def test_the_body_size_default_matches_the_http_layer() -> None:
    """One number, two modules that may not import each other.

    `64 * 1024 * 1024` was written at three sites in `config` and once more in
    `http_security`, so a raised ceiling could have been applied by the loader
    and refused by the server. The import that would make this structural runs
    the wrong way past the layer guard — `http_security` is a front-end module
    — so the equality is asserted instead.
    """
    from pzi.http_security import DEFAULT_MAX_BODY_BYTES as http_default

    assert config_module.DEFAULT_MAX_BODY_BYTES == http_default
