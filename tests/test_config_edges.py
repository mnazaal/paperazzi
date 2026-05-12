"""Edge-case tests covering uncovered lines in src/pzi/config.py.

Lines covered: 124-125, 129-130, 153, 157, 160, 178, 182, 185, 189, 228, 236.
"""

from pzi.config import resolve_bib, validate_app_config, validate_bib_config

HOME = "/home/tester"


# ── lines 124-125: bibs item that is not a Mapping ──────────────────────────

def test_validate_app_config_bibs_item_not_a_mapping() -> None:
    config, errors = validate_app_config(
        {
            "bibs": [
                {"name": "ml", "path": "~/bib/ml.bib"},
                "not a mapping",
            ]
        },
        home_dir=HOME,
    )
    assert config is None
    assert "bibs[1] must be a mapping" in errors


# ── lines 129-130: bib item validation errors propagated ────────────────────

def test_validate_app_config_bibs_validation_error_propagation() -> None:
    config, errors = validate_app_config(
        {
            "bibs": [
                {"name": "ml", "path": "~/bib/ml.bib"},
                {"name": "", "path": ""},
            ]
        },
        home_dir=HOME,
    )
    assert config is None
    assert any(e.startswith("bibs[1].") for e in errors)


# ── line 153: unpaywall_email not a string ──────────────────────────────────

def test_validate_app_config_unpaywall_email_not_a_string() -> None:
    config, errors = validate_app_config(
        {
            "unpaywall_email": 42,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert config is None
    assert "unpaywall_email must be a string when provided" in errors


# ── line 157: unpaywall_email_cmd not a string ──────────────────────────────

def test_validate_app_config_unpaywall_email_cmd_not_a_string() -> None:
    config, errors = validate_app_config(
        {
            "unpaywall_email_cmd": 42,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert config is None
    assert "unpaywall_email_cmd must be a string when provided" in errors


# ── line 160: early return after unpaywall errors (blocks s2 check) ─────────

def test_validate_app_config_unpaywall_errors_early_return() -> None:
    config, errors = validate_app_config(
        {
            "unpaywall_email": 1,
            "semantic_scholar_api_key": 2,  # should not be checked
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert config is None
    assert "unpaywall_email must be a string when provided" in errors
    assert not any("semantic_scholar" in e for e in errors)


# ── line 178: semantic_scholar_api_key not a string ─────────────────────────

def test_validate_app_config_semantic_scholar_api_key_not_a_string() -> None:
    config, errors = validate_app_config(
        {
            "semantic_scholar_api_key": 99,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert config is None
    assert "semantic_scholar_api_key must be a string when provided" in errors


# ── line 182: semantic_scholar_api_key_cmd not a string ─────────────────────

def test_validate_app_config_semantic_scholar_api_key_cmd_not_a_string() -> None:
    config, errors = validate_app_config(
        {
            "semantic_scholar_api_key_cmd": 99,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert config is None
    assert "semantic_scholar_api_key_cmd must be a string when provided" in errors


# ── line 185: early return after s2 errors (blocks flaresolverr check) ──────

def test_validate_app_config_s2_errors_early_return() -> None:
    config, errors = validate_app_config(
        {
            "semantic_scholar_api_key": 1,
            "semantic_scholar_api_key_cmd": 2,
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert config is None
    assert len(errors) == 2


# ── line 189: flaresolverr_url invalid http → set to None ───────────────────

def test_validate_app_config_flaresolverr_url_not_valid_http() -> None:
    config, errors = validate_app_config(
        {
            "flaresolverr_url": "ftp://bad.url",
            "bibs": [{"name": "ml", "path": "~/ml.bib"}],
        },
        home_dir=HOME,
    )
    assert errors == []
    assert config is not None
    assert config["flaresolverr_url"] is None


# ── line 228: resolve_bib returns the single default ────────────────────────

def test_resolve_bib_returns_single_default_among_many() -> None:
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
    assert resolve_bib(bibs, None) == bibs[1]


# ── line 236: _normalize_path with literal "~" ──────────────────────────────

def test_validate_bib_config_path_literal_tilde() -> None:
    config, errors = validate_bib_config(
        {"name": "ml", "path": "~"},
        home_dir=HOME,
    )
    assert errors == []
    assert config is not None
    assert config["path"] == "/home/tester"
