from pathlib import Path

from pzi.config import default_config_path, load_config_file, load_default_config


def test_default_config_path_uses_xdg_style_location() -> None:
    assert default_config_path("/home/tester") == "/home/tester/.config/pzi/config.toml"


def test_load_config_file_reads_and_validates_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
translation_server_url = "http://127.0.0.1:1969"

[[bibs]]
name = "ml"
path = "~/bib/ml.bib"
default = true
""".strip()
    )

    result = load_config_file(str(config_path), home_dir="/home/tester")

    assert result == {
        "config": {
            "translation_server_url": "http://127.0.0.1:1969",
            "bibs": [
                {
                    "name": "ml",
                    "path": "/home/tester/bib/ml.bib",
                    "papers_dir": "/home/tester/bib/papers",
                    "default": True,
                }
            ],
            "api_listen_host": "127.0.0.1",
            "api_listen_port": 8765,
            "api_auth_token": None,
            "api_allowed_origins": None,
            "api_max_body_bytes": 67108864,
            "unpaywall_email": None,
            "unpaywall_email_cmd": None,
            "semantic_scholar_api_key": None,
            "semantic_scholar_api_key_cmd": None,
            "flaresolverr_url": None,
            "browser_pdf_cmd": None,
        },
        "errors": [],
        "path": str(config_path),
    }


def test_load_config_file_reports_missing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"

    result = load_config_file(str(config_path), home_dir="/home/tester")

    assert result == {
        "config": None,
        "errors": [f"config file not found: {config_path}"],
        "path": str(config_path),
    }


def test_load_config_file_reports_invalid_utf8(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_bytes(b"\xff\xfe\x00")

    result = load_config_file(str(config_path), home_dir="/home/tester")

    assert result == {
        "config": None,
        "errors": ["config file must be valid UTF-8 text"],
        "path": str(config_path),
    }


def test_load_config_file_reports_invalid_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("bibs = [")

    result = load_config_file(str(config_path), home_dir="/home/tester")

    assert result["config"] is None
    assert result["path"] == str(config_path)
    assert len(result["errors"]) == 1
    assert result["errors"][0].startswith("invalid TOML:")


def test_load_config_file_returns_validation_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
translation_server_url = "ftp://example.com"
bibs = []
""".strip()
    )

    result = load_config_file(str(config_path), home_dir="/home/tester")

    assert result == {
        "config": None,
        "errors": [
            "translation_server_url must be an http or https URL",
            "bibs must be a non-empty list",
        ],
        "path": str(config_path),
    }


def test_load_default_config_uses_default_path(tmp_path: Path) -> None:
    home_dir = str(tmp_path)
    config_path = tmp_path / ".config" / "pzi"
    config_path.mkdir(parents=True)
    (config_path / "config.toml").write_text(
        """
[[bibs]]
name = "ml"
path = "~/bib/ml.bib"
""".strip()
    )

    result = load_default_config(home_dir=home_dir)

    assert result["errors"] == []
    assert result["path"] == str(config_path / "config.toml")
    assert result["config"] is not None
