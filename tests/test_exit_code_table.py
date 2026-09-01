"""The exit-code table, pinned — item 421.

`tests/test_cli_contract.py` asserts individual codes case by case, which is
real coverage of behaviour and says nothing about the table *as a whole*. The
1.0 freeze is on the table: one meaning per code, so a script can branch on the
status alone. That needs three claims checked, not more one-off cases:

1. the README table and `pzi.exit_codes` agree, in both directions;
2. every documented code is actually reachable;
3. the two signal codes are mapped where they are documented to be.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from pzi import exit_codes
from pzi.cli import main, run_cli
from pzi.commands.common import batch_exit_code

README = Path(__file__).parent.parent / "README.md"

MINIMAL_CONFIG = """
[[bibs]]
name = "ml"
path = "{bib_path}"
default = true
"""

ONE_ENTRY = """@article{smith2020,
  title = {A Title},
  author = {Smith, Jane},
  year = {2020},
}
"""


def _documented_codes() -> set[int]:
    """The codes the README's exit-code table lists.

    The table has a combined `130 / 141` row, so a row can name more than one
    code — parsed rather than assumed, because that row is exactly the kind of
    thing a naive parser silently drops.
    """
    text = README.read_text(encoding="utf-8")
    table = re.search(r"\| Code \| Meaning \|\n\|[-| ]+\|\n((?:\|.*\n)+)", text)
    assert table, "the exit-code table is missing from README.md"
    codes: set[int] = set()
    for row in table.group(1).strip().splitlines():
        first_cell = row.split("|")[1]
        codes.update(int(n) for n in re.findall(r"\d+", first_cell))
    return codes


def _defined_codes() -> dict[str, int]:
    return {
        name: value
        for name, value in vars(exit_codes).items()
        if name.isupper() and isinstance(value, int)
    }


def test_the_readme_table_and_the_exit_codes_module_agree() -> None:
    """Drift in either direction is a broken promise.

    A code defined but undocumented is one a script cannot branch on; a code
    documented but not defined is one that will never be returned. Both look
    fine from inside the code that does not use them.
    """
    documented = _documented_codes()
    defined = set(_defined_codes().values())

    assert documented == defined, (
        "the README exit-code table and `pzi.exit_codes` disagree.\n"
        f"  documented but not defined: {sorted(documented - defined)}\n"
        f"  defined but not documented: {sorted(defined - documented)}\n"
        "Both are frozen at 1.0 — fix whichever is wrong and say so in "
        "CHANGELOG.md."
    )


def _run(argv: list[str], home: Path) -> int:
    return run_cli(argv, home_dir=str(home), stdout=StringIO(), stderr=StringIO())


def _library(tmp_path: Path, text: str = "") -> Path:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(text, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(MINIMAL_CONFIG.format(bib_path=bib_path), encoding="utf-8")
    return config_path


def test_every_documented_exit_code_is_reachable(tmp_path: Path) -> None:
    """One invocation per code, in one place, offline.

    Collected here rather than scattered so that "every code in the table is
    reachable" is a claim something checks, instead of an impression left by
    forty tests that each happen to assert one.
    """
    config = _library(tmp_path, ONE_ENTRY)
    reached: dict[int, str] = {}

    def record(code: int, how: str) -> None:
        reached[code] = how

    record(
        _run(["entries", "--config", str(config)], tmp_path),
        "pzi entries on a readable library",
    )
    record(
        _run(["search", "--query", "nothing-matches-this", "--config", str(config)], tmp_path),
        "pzi search with no matches",
    )
    record(
        _run(["entries", "smith2020", "--stats", "--config", str(config)], tmp_path),
        "pzi entries with two mutually exclusive flags",
    )
    record(
        _run(["delete", "nosuchkey", "--force", "--config", str(config)], tmp_path),
        "pzi delete of a citekey that does not exist",
    )
    record(
        _run(["entries", "--target", str(tmp_path / "missing.bib"), "--config", str(config)],
             tmp_path),
        "pzi entries against a --target that is not there",
    )
    # PARTIAL is the one code with no offline single-invocation route: it needs a
    # batch where one item succeeds and another fails, and every offline batch
    # this suite can build fails wholly (which is ENVIRONMENT, deliberately —
    # see `batch_exit_code`). Exercised at the shared helper instead, which is
    # where all five batch commands get their answer, and named here so the gap
    # is visible rather than implied.
    record(batch_exit_code(succeeded=1, failed=1), "batch_exit_code(1 ok, 1 failed)")

    for code in (
        exit_codes.OK,
        exit_codes.FINDINGS,
        exit_codes.USAGE,
        exit_codes.NOT_FOUND,
        exit_codes.PARTIAL,
        exit_codes.ENVIRONMENT,
    ):
        assert code in reached, (
            f"exit code {code} is documented but nothing here produced it — "
            "either it is unreachable or this test stopped covering it"
        )


def test_the_signal_codes_are_mapped_where_they_are_documented() -> None:
    """130 and 141 come from `main`, not from any command.

    Checked at the handler rather than by signalling a real subprocess: racing a
    process's startup to deliver SIGINT is precisely the flaky test this suite
    does not need, and the mapping is the whole of the contract. The limitation
    is that nothing here proves a *real* Ctrl-C reaches this handler.
    """
    with patch("pzi.cli.run_cli", side_effect=KeyboardInterrupt), \
            patch("sys.argv", ["pzi", "entries"]):
        assert main() == exit_codes.INTERRUPTED

    # `os.dup2` is neutralised, not exercised: the handler points stdout at
    # /dev/null so the interpreter's final flush cannot re-raise on shutdown,
    # and doing that for real inside pytest redirects pytest's own capture fd
    # and breaks the rest of the session with `OSError: Bad file descriptor`.
    # The contract under test is the mapping to 141; the redirect is plumbing,
    # and the handler already treats a failing `dup2` as non-fatal.
    with patch("pzi.cli.run_cli", side_effect=BrokenPipeError), \
            patch("sys.argv", ["pzi", "entries"]), \
            patch("pzi.cli.os.dup2"):
        assert main() == exit_codes.BROKEN_PIPE


def test_no_two_codes_share_a_meaning() -> None:
    """"One meaning per code" is the property the table promises a script."""
    defined = _defined_codes()
    assert len(set(defined.values())) == len(defined), (
        f"two names share an exit code: {defined}"
    )


def test_sigterm_to_the_server_is_the_documented_interrupted_code() -> None:
    """`pzi server` catches SIGTERM, so it is 130 too — and 130 has to say so.

    systemd's default stop signal is SIGTERM, so the supervised path is the one
    that runs in production, and it went through a handler documented as SIGINT
    only. Decided rather than left ambiguous: one code covers both, because the
    only sender of SIGTERM here is a supervisor that already knows what it sent.
    Delivered for real to this process, so the claim is about the handler the
    server actually installs rather than about a hand-written mapping.
    """
    import os
    import signal

    from pzi.cli_parser import build_parser

    # The shim moved to its seam (`backend_session` installs it for every
    # command, item 585); `pzi server` imports and uses the same one.
    from pzi.ts_backend import sigterm_unwinds

    raised = False
    with sigterm_unwinds():
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except KeyboardInterrupt:
            raised = True
    assert raised, "SIGTERM did not reach the handler `pzi server` installs"

    # …and that is the interrupt `main` maps, so the process status is 130.
    with patch("pzi.cli.run_cli", side_effect=KeyboardInterrupt), \
            patch("sys.argv", ["pzi", "server"]):
        assert main() == exit_codes.INTERRUPTED

    # The `pzi --help` epilog is where a user reads this, so it names both.
    epilog = build_parser().epilog or ""
    assert "SIGTERM" in epilog and "SIGINT" in epilog, epilog
