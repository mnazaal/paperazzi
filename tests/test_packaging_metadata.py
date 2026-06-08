import tomllib
from pathlib import Path


def test_package_data_includes_type_marker() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())

    package_data = data["tool"]["setuptools"]["package-data"]["pzi"]

    assert "py.typed" in package_data


def test_package_classifier_matches_beta_readme_status() -> None:
    data = tomllib.loads(Path("pyproject.toml").read_text())
    readme = Path("README.md").read_text()

    assert "**Status: beta.**" in readme
    assert "Development Status :: 4 - Beta" in data["project"]["classifiers"]


def test_ci_pins_node_for_browser_extension_tests() -> None:
    ci = Path(".github/workflows/ci.yml").read_text()

    assert "actions/setup-node@v4" in ci
    assert "node-version" in ci


def test_readme_uses_target_option_name() -> None:
    readme = Path("README.md").read_text()

    assert "--bib <target>" not in readme
    assert "--target <target>" in readme
