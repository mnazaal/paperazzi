"""Fixtures for the live smoke job, and the mechanism that keeps it honest.

The live job is the only place the **translation-server** metadata path is
exercised. It is `continue-on-error: true` in CI on purpose — a third-party
outage must not block a merge — and it skips rather than fails whenever the
backend cannot start or a provider answers nothing. Stacked, those produce a
green run that captured absolutely nothing, indistinguishable from a green run
that captured everything.

So this file does two things beyond providing fixtures: it **fails the session**
when `PZI_LIVE=1` and no translation-server capture actually ran, naming which
of the three causes it was, and it **writes a ran-vs-skipped summary** to the
GitHub step summary so the outcome is legible without opening the log.
"""

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pzi.ts_backend import backend_session

#: Set by `running_translation_server` so the session hook can tell "the backend
#: never came up" apart from "it came up and nothing was captured". A plain
#: module global rather than a stash key: the fixtures live in this file too, so
#: there is nothing to thread through, and the alternative is a cross-module
#: import from a test file.
_LIVE_RUN: dict[str, object] = {
    "translation_server_started": False,
    "outcomes": [],  # list[tuple[str, str, str]] — (nodeid, outcome, reason)
}

#: Tests whose passing proves a real translation-server capture happened. Named
#: rather than inferred from the module, so moving a test does not silently
#: empty the set that the session gate counts.
#:
#: One name, not two. `test_live_add_arxiv_url_metadata` deliberately does not
#: assert *which* provider answered — an arXiv URL is classified `doi`, so a
#: capture Crossref or OpenAlex resolved is a pass there — and counting it here
#: let the gate report "real translation-server capture: yes" for a run in which
#: the server answered nothing. That is precisely the state this gate exists to
#: make visible, so the one test that pins `provider == "translation_server"` is
#: the only one whose pass proves it.
_CAPTURE_TESTS = frozenset({
    "test_live_add_oa_doi_metadata",
})


def live_enabled() -> bool:
    return os.environ.get("PZI_LIVE") == "1"


def pytest_collection_modifyitems(config, items):
    if live_enabled():
        return
    skip_live = pytest.mark.skip(reason="set PZI_LIVE=1 to run live smoke tests")
    for item in items:
        if "tests/live" in str(item.path):
            item.add_marker(skip_live)


def _write_config(bib_path: str, config_path: str) -> str:
    """Write a minimal pzi config pointing at a temp bib."""
    bib_path_abs = str(Path(bib_path).resolve())
    config_dir = os.path.dirname(config_path)
    papers_dir = os.path.join(config_dir, "papers")
    config_text = f"""
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "smoke"
path = "{bib_path_abs}"
papers_dir = "{papers_dir}"
default = true
"""
    config_dir_abs = os.path.dirname(config_path)
    os.makedirs(config_dir_abs, exist_ok=True)
    Path(config_path).write_text(config_text, encoding="utf-8")
    return config_path


@pytest.fixture(scope="module")
def running_translation_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Start a real translation-server for the live tests, shared to avoid
    re-cloning/npm-installing per test. Skips (not fails) if it can't come up —
    this is a network-dependent smoke job, not a gate. The skip is recorded, so
    the end of the session can report that nothing ran and why.
    """
    data_home = tmp_path_factory.mktemp("ts-data")
    config: dict[str, object] = {
        "translation_server_url": "http://127.0.0.1:1969",
        "pzi_data_home": str(data_home),
    }
    with backend_session(
        config, home_dir=os.path.expanduser("~"),
        interactive=False, stdout=sys.stdout, stderr=sys.stderr,
    ) as backend:
        if not backend["ready"]:
            pytest.skip("translation-server could not be started for live tests")
        _LIVE_RUN["translation_server_started"] = True
        yield


@pytest.fixture
def live_config_path(tmp_path: Path, running_translation_server: None) -> str:
    bib_path = tmp_path / "smoke.bib"
    config_path = tmp_path / "config.toml"
    _write_config(str(bib_path), str(config_path))
    return str(config_path)


@pytest.fixture
def contact_email() -> str | None:
    return os.environ.get("PZI_CONTACT_EMAIL") or os.environ.get("PZI_UNPAYWALL_EMAIL")


@pytest.fixture
def unpaywall_email() -> str | None:
    return os.environ.get("PZI_UNPAYWALL_EMAIL") or os.environ.get("PZI_CONTACT_EMAIL")


@pytest.fixture
def s2_api_key() -> str | None:
    return os.environ.get("PZI_S2_API_KEY")


# ---------------------------------------------------------------------------
# Reporting: what actually ran, and whether that was enough
# ---------------------------------------------------------------------------


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record one line per test outcome, including the reason for a skip.

    The call phase carries pass/fail; a skip raised inside a fixture surfaces in
    setup instead, which is exactly the case that matters here — the whole
    module skipping because the backend never started.
    """
    if not live_enabled() or "tests/live" not in str(report.fspath):
        return
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        reason = ""
        if report.outcome == "skipped" and isinstance(report.longrepr, tuple):
            reason = report.longrepr[2].removeprefix("Skipped: ")
        outcomes: list = _LIVE_RUN["outcomes"]  # type: ignore[assignment]
        outcomes.append((report.nodeid, report.outcome, reason))


def _capture_ran() -> bool:
    outcomes: list = _LIVE_RUN["outcomes"]  # type: ignore[assignment]
    return any(
        outcome == "passed" and nodeid.rsplit("::", 1)[-1] in _CAPTURE_TESTS
        for nodeid, outcome, _reason in outcomes
    )


def _diagnosis() -> str:
    """Why no capture ran — the three causes are not the same problem."""
    if not _LIVE_RUN["translation_server_started"]:
        return (
            "the translation-server never started, so no capture was attempted. "
            "This is the failure that used to look identical to a clean run."
        )
    outcomes: list = _LIVE_RUN["outcomes"]  # type: ignore[assignment]
    skipped = [
        reason
        for nodeid, outcome, reason in outcomes
        if outcome == "skipped" and nodeid.rsplit("::", 1)[-1] in _CAPTURE_TESTS
    ]
    if skipped:
        return (
            "the translation-server started but returned no usable metadata: "
            + "; ".join(skipped)
        )
    return (
        "the translation-server started but no capture test passed — check "
        "whether the capture fell back to another provider, which the tests "
        "now assert against"
    )


def _summary_lines() -> list[str]:
    outcomes: list = _LIVE_RUN["outcomes"]  # type: ignore[assignment]
    ran = [n for n, o, _ in outcomes if o == "passed"]
    failed = [n for n, o, _ in outcomes if o == "failed"]
    skipped = [(n, r) for n, o, r in outcomes if o == "skipped"]
    lines = [
        "## pzi live smoke",
        "",
        f"- translation-server started: **{'yes' if _LIVE_RUN['translation_server_started'] else 'no'}**",
        f"- passed: **{len(ran)}**, failed: **{len(failed)}**, skipped: **{len(skipped)}**",
        f"- real translation-server capture: **{'yes' if _capture_ran() else 'NO'}**",
    ]
    if not _capture_ran():
        lines += ["", f"> **Nothing was captured.** {_diagnosis()}"]
    if skipped:
        lines += ["", "| skipped test | reason |", "|---|---|"]
        lines += [f"| `{n}` | {r or '—'} |" for n, r in skipped]
    return lines


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Report what ran, and fail an empty live run.

    Without this the job's own `continue-on-error` plus two layers of skips add
    up to a green check for a run that exercised nothing at all — which is the
    state PLAN item 412 describes from the other side ("every capture on record
    fell back to Crossref").
    """
    if not live_enabled():
        return
    lines = _summary_lines()
    print("\n" + "\n".join(lines), file=sys.stderr)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    if not _capture_ran() and exitstatus == 0:
        # Deliberately not a skip and not a warning: a run that captured nothing
        # has told us nothing, and the only way that is visible is a non-zero
        # exit. The job stays `continue-on-error`, so this reddens the step
        # without blocking a merge.
        session.exitstatus = 1
