import os

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if os.environ.get("PZI_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="set PZI_LIVE=1 to run live smoke tests")
    for item in items:
        if "tests/live" in str(item.path):
            item.add_marker(skip_live)


@pytest.fixture
def contact_email() -> str | None:
    return os.environ.get("PZI_CONTACT_EMAIL") or os.environ.get("PZI_UNPAYWALL_EMAIL")


@pytest.fixture
def unpaywall_email() -> str | None:
    return os.environ.get("PZI_UNPAYWALL_EMAIL") or os.environ.get("PZI_CONTACT_EMAIL")


@pytest.fixture
def s2_api_key() -> str | None:
    return os.environ.get("PZI_S2_API_KEY")
