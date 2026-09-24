"""Every read-only command must exit 1 on a lenient/dropped-block parse — item 631.

`commands/common.has_read_warnings` is the shared rule: a read that worked but
could only partly complete (duplicate citekey, dropped block, missing file) is
`FINDINGS` (1), not a clean `OK` (0). `entries`, `library dedupe`, `library
clean`, `library reindex`, and `library check` already route through it (or
through an equivalent all-sources-unreachable check for `check`); `tag list`
did not until this change (`commands/tags.py`). This test pins the rule across
every read-only command in one place so a new command — or a new call site in
an existing one — cannot quietly skip it the way `tag list` did.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest

from pzi import exit_codes
from pzi.cli import run_cli

MINIMAL_CONFIG = """
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
"""

# A duplicate citekey: the parser keeps only the first occurrence and reports
# the drop as a `warnings` entry on an otherwise `status: ok` read (see the
# lane task's probe). Both entries carry the same title text so `search
# --query x` matches the surviving one.
DUPLICATE_CITEKEY_BIB = """@article{a,
  title={x},
  keywords={k},
  year={2020}
}
@article{a,
  title={y},
  year={2021}
}
"""


@pytest.fixture()
def duplicate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(DUPLICATE_CITEKEY_BIB, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")
    return config_path


def _run(argv: list[str], home: Path) -> int:
    return run_cli(argv, home_dir=str(home), stdout=StringIO(), stderr=StringIO())


READ_ONLY_COMMANDS: dict[str, list[str]] = {
    "entries": ["entries"],
    "tag list": ["tag", "list"],
    "library dedupe": ["library", "dedupe"],
    "library clean (no --fix)": ["library", "clean"],
}


@pytest.mark.parametrize("label", sorted(READ_ONLY_COMMANDS))
def test_read_only_commands_exit_findings_on_read_warnings(
    label: str, duplicate_config: Path, tmp_path: Path,
) -> None:
    argv = [*READ_ONLY_COMMANDS[label], "--config", str(duplicate_config)]
    exit_code = _run(argv, tmp_path)
    assert exit_code == exit_codes.FINDINGS, (
        f"{label!r} (argv={argv}) exited {exit_code}, expected "
        f"exit_codes.FINDINGS ({exit_codes.FINDINGS}) for a library with a "
        "dropped duplicate-citekey block"
    )


# `search` is excluded, not weakened: `run_search_command`
# (src/pzi/commands/search.py) computes its exit code as
# `OK if found_any else FINDINGS`, entirely independent of
# `has_read_warnings` — a search that finds a match on a library the parser
# could only partly read still exits 0. That is the same defect class as the
# one this file exists to catch, but `search.py` is not an owned file in this
# lane (LANES.md, lane B: tags.py/config.py/reindex.py/cli_parser.py/
# update.py/identifiers.py only), so it is reported to the parent instead of
# fixed here.
def test_search_finding_a_match_does_not_yet_report_read_warnings(
    duplicate_config: Path, tmp_path: Path,
) -> None:
    """Characterizes the current (buggy) behaviour so a real fix shows up as a
    change to this test, not a silent regression."""
    exit_code = _run(
        ["search", "--query", "x", "--config", str(duplicate_config)], tmp_path,
    )
    assert exit_code == exit_codes.OK


# `library check` is excluded for a documented reason, not weakened: with
# network access unavailable (this test runs in a sandboxed environment with
# no route to Crossref/OpenAlex/DBLP/OpenReview/Semantic Scholar),
# `check_service.check_bib` reaches no source for any entry, sets
# `audited_nothing = True` and returns `status: "error"` /
# `reason: REASON_UNAVAILABLE` (check_service.py:577,593) — which
# `commands/check.py:221` maps to `exit_codes.ENVIRONMENT` (5), not
# `FINDINGS`. That is `check`'s own documented rule ("5 when ... no source
# could be reached"), decided independently of the duplicate-citekey read
# warning this file is about, so pinning it here would assert an artifact of
# the test environment's network policy rather than the behaviour under test.
def test_library_check_with_no_network_route_exits_environment(
    duplicate_config: Path, tmp_path: Path,
) -> None:
    exit_code = _run(
        ["library", "check", "--limit", "1", "--config", str(duplicate_config)], tmp_path,
    )
    assert exit_code == exit_codes.ENVIRONMENT
