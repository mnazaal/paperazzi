from pzi.config import (
    default_config_path,
    default_data_home,
    derive_papers_dir,
    resolve_bib,
    validate_app_config,
    validate_bib_config,
    xdg_config_home,
    xdg_data_home,
)

HOME = "/home/tester"


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
        "translation_server_url": "http://127.0.0.1:1969",
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
        "api_allowed_origins": None,
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
        "promote_confidence_threshold": 3,
        "metadata_cache_ttl": 0,
        "browser_hook": True,
        "pzi_data_home": f"{HOME}/.local/share/pzi",
        "node_path": None,
        "api_url": "http://127.0.0.1:8765",
        "browser_profile_path": None,
        "browser_engine": "chromium",
        "rate_limit_rpm": 60,
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

    assert resolve_bib([bib], None) == bib


def test_resolve_bib_by_explicit_name() -> None:
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
            "default": True,
        },
    ]

    assert resolve_bib(bibs, "ml") == bibs[0]


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

    assert resolve_bib(bibs, None) is None


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
