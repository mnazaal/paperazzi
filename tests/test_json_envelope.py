"""The `--json` envelope, pinned — item 422.

The README promises one JSON document on stdout for every command that offers
`--json`, "including when it fails, so a script never has to scrape stderr to
classify an error". That promise freezes at 1.0 and nothing checked it.

Two halves, because the envelope has two:

- `build_envelope` (`cli_json.py:31`) fixes five keys — `command`, `status`,
  `bib_name`, `items`, `errors` — and that is one shared contract, asserted
  generically here over every `--json` command;
- everything else a service returns is passed through, so the per-command keys
  are open-ended and get a snapshot, the same technique as `test_cli_surface`.
"""

from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from pzi.cli import run_cli
from pzi.cli_json import ENVELOPE_COMMANDS
from pzi.cli_parser import build_parser
from tests.test_cli_surface import _subparsers

SNAPSHOT = Path(__file__).parent / "fixtures" / "json_envelopes.txt"
UPDATE_ENV = "PZI_UPDATE_JSON_ENVELOPES"

ENVELOPE_KEYS = ("command", "status", "bib_name", "items", "errors")

ONE_ENTRY = """@article{smith2020,
  title = {A Title},
  author = {Smith, Jane},
  year = {2020},
}
"""

#: One offline invocation per `--json` command. Commands needing the network for
#: their *success* path are invoked so they fail offline — the envelope contract
#: covers failure too, and that is the path a consumer branches on. The point of
#: the table is that every `--json` command is listed: `test_every_json_command_
#: is_covered` fails if the parser grows one that is not here.
INVOCATIONS: dict[str, list[str]] = {
    "add": ["add", "--target", "MISSING", "not-a-real-doi"],
    "library check": ["library", "check", "--target", "MISSING"],
    "delete": ["delete", "smith2020", "--force"],
    "doctor": ["doctor", "--config-only"],
    "entries": ["entries"],
    "library clean": ["library", "clean"],
    "library dedupe": ["library", "dedupe"],
    "library list": ["library", "list"],
    "library merge": ["library", "merge", "smith2020", "nosuch2021"],
    "library reindex": ["library", "reindex"],
    "import": ["import", "MISSING"],
    "inbox": ["inbox", "MISSING"],
    "pdf attach": ["pdf", "attach", "smith2020", "MISSING"],
    "pdf retry": ["pdf", "retry", "nosuch2021"],
    "search": ["search", "--query", "Title"],
    "tag add": ["tag", "add", "smith2020", "probe"],
    "tag list": ["tag", "list"],
    "tag remove": ["tag", "remove", "smith2020", "probe"],
    "update": ["update", "--target", "MISSING"],
}


def _json_commands() -> set[str]:
    """Every command path that accepts `--json`, from the parser itself."""
    found: set[str] = set()

    def walk(parser: Any, path: tuple[str, ...]) -> None:
        if path and any(
            "--json" in action.option_strings for action in parser._actions
        ):
            found.add(" ".join(path))
        for name, sub in _subparsers(parser).items():
            walk(sub, (*path, name))

    walk(build_parser(), ())
    return found


def test_every_json_command_is_covered() -> None:
    """The table above must not fall behind the parser.

    A new `--json` command that nobody adds here would be silently exempt from
    the envelope contract — which is exactly the kind of quiet gap this file
    exists to close.
    """
    assert _json_commands() == set(INVOCATIONS), (
        f"  accept --json but are not exercised: {sorted(_json_commands() - set(INVOCATIONS))}\n"
        f"  exercised but no longer accept --json: {sorted(set(INVOCATIONS) - _json_commands())}"
    )


def _library(home: Path) -> Path:
    """A one-entry library and a config naming it, inside *home*.

    Built per command, never shared. Sharing one library across the table would
    make each envelope depend on what ran before it — `delete` sorts ahead of
    `tag`, so a shared library had `tag add` reporting an error about an entry
    `delete` had just removed, and the snapshot recorded that as if it were the
    shape of a successful tag write.
    """
    home.mkdir(parents=True, exist_ok=True)
    bib_path = home / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = home / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    return config_path


#: Invocations whose envelope names a *flag*, not the command path. They cannot
#: live in `INVOCATIONS`, which `test_every_json_command_is_covered` pins to the
#: parser's command paths — and that is the whole reason these went unnoticed.
FLAG_INVOCATIONS: dict[str, list[str]] = {
    "add --from-file": ["add", "--from-file", "MISSING"],
    "entries --stats": ["entries", "--stats"],
    "update --promote": ["update", "--promote", "--target", "MISSING"],
}

#: Invocations run against a config that does not exist. Both forms of
#: `doctor` that this file cannot run on their success path — the bare form
#: probes live services, `--reinstall-server` downloads a Node runtime — still
#: emit their labelled envelope on the *failure* path, because `doctor_check`
#: returns before any probe when the config fails to load and the reinstall
#: reaches its reporting before `ensure_node`. So the label contract is
#: exercised offline without either side effect. This replaced an exemption
#: list: the pre-0.2.0 review showed both of its justifications were true of
#: the success paths only, and label verification never needed those.
MISSING_CONFIG_INVOCATIONS: dict[str, list[str]] = {
    "doctor (bare, failing config)": ["doctor"],
    "doctor --reinstall-server (failing config)": ["doctor", "--reinstall-server"],
}

#: Label each missing-config invocation must produce.
MISSING_CONFIG_LABELS: dict[str, str] = {
    "doctor (bare, failing config)": "doctor",
    "doctor --reinstall-server (failing config)": "doctor --reinstall-server",
}


def _run_json(command: str, home: Path) -> dict[str, Any]:
    if command in MISSING_CONFIG_INVOCATIONS:
        home.mkdir(parents=True, exist_ok=True)
        config = home / "absent-config.toml"  # deliberately never written
        spec = MISSING_CONFIG_INVOCATIONS[command]
    else:
        config = _library(home)
        spec = {**INVOCATIONS, **FLAG_INVOCATIONS}[command]
    argv = [arg.replace("MISSING", str(home / "missing.bib")) for arg in spec]
    stdout = StringIO()
    run_cli(
        [*argv, "--json", "--config", str(config)],
        home_dir=str(home),
        stdout=stdout,
        stderr=StringIO(),
    )
    raw = stdout.getvalue()
    assert raw.strip(), f"`pzi {command} --json` wrote nothing to stdout"
    # Exactly one document: a consumer reads stdout once and parses it once.
    return json.loads(raw)


@pytest.mark.parametrize("command", sorted({**INVOCATIONS, **FLAG_INVOCATIONS}))
def test_the_envelope_names_a_declared_command(command: str, tmp_path: Path) -> None:
    """The `command` a runner emits has to be one somebody decided about.

    `cli_json.ENVELOPE_COMMANDS` is the declared set, and
    `tests/test_surface_parity.py` demands a cross-surface row for each name in
    it. That only means anything if the set matches what the runners actually
    emit — otherwise the parity matrix is complete with respect to a list, not
    with respect to the CLI.

    So this half is *observed*. It has to be: five of the names are flags, and
    a flag is exactly what no parser walk can see.

    Membership **and** identity. Membership alone let two runners swap their
    labels with the whole suite green — `entries --stats` reporting
    `update --promote` is still "a declared name", just the wrong one — which
    the pre-0.2.0 review proved by doing exactly that. `startswith` rather
    than equality because `INVOCATIONS` keys are command paths and several
    runners rightly label the *mode* (`doctor` emits `doctor --config-only`);
    for a flag-level invocation the prefix is the whole label, so the swap
    cannot survive it.
    """
    envelope = _run_json(command, tmp_path / command.replace(" ", "-"))

    assert envelope["command"] in ENVELOPE_COMMANDS, (
        f"`pzi {command} --json` labelled its envelope "
        f"{envelope['command']!r}, which is not in cli_json.ENVELOPE_COMMANDS "
        "— register it there and give it a row in the surface-parity matrix"
    )
    assert envelope["command"].startswith(command), (
        f"`pzi {command} --json` labelled its envelope {envelope['command']!r}"
    )


def test_every_declared_envelope_command_is_actually_produced(
    tmp_path: Path,
) -> None:
    """The other direction: a registered name nothing emits is a dead entry.

    Without this, `ENVELOPE_COMMANDS` could drift into a wish-list — and the
    parity matrix would be complete with respect to names that no longer exist,
    which is worse than incomplete because it reads as covered.

    No exemptions. An earlier version exempted both `doctor` forms with
    justifications that were true only of their success paths; the failing-
    config invocations in `MISSING_CONFIG_INVOCATIONS` produce the same labels
    with no network and no side effects, so every declared name is observed.
    """
    all_invocations = sorted(
        {**INVOCATIONS, **FLAG_INVOCATIONS, **MISSING_CONFIG_INVOCATIONS}
    )
    observed = {
        _run_json(command, tmp_path / f"observe-{index}")["command"]
        for index, command in enumerate(all_invocations)
    }

    assert observed == set(ENVELOPE_COMMANDS), (
        f"  declared but never produced: "
        f"{sorted(set(ENVELOPE_COMMANDS) - observed)}\n"
        f"  produced but not declared: {sorted(observed - set(ENVELOPE_COMMANDS))}"
    )


@pytest.mark.parametrize(
    "command",
    sorted({**INVOCATIONS, **FLAG_INVOCATIONS, **MISSING_CONFIG_INVOCATIONS}),
)
def test_the_envelope_holds_for_every_json_command(
    command: str, tmp_path: Path
) -> None:
    """The five keys, the right types, whether the command succeeded or not."""
    envelope = _run_json(command, tmp_path / command.replace(" ", "-"))

    for key in ENVELOPE_KEYS:
        assert key in envelope, f"`pzi {command} --json` envelope is missing {key!r}"
    # `startswith`, not equality: several runners name the *mode* rather than
    # the command — `doctor --config-only`, `entries --stats`, `add --from-file`,
    # `update --promote` — because those modes return differently shaped
    # results, and a consumer switching on `command` wants to tell them apart.
    # The exact strings are pinned by the snapshot below; what must hold
    # everywhere is that the field starts with the command that was invoked.
    expected_prefix = MISSING_CONFIG_LABELS.get(command, command)
    assert envelope["command"].startswith(expected_prefix), (
        f"envelope says {envelope['command']!r}, invoked as {command!r}"
    )
    assert envelope["status"] in {"ok", "error"}
    assert envelope["bib_name"] is None or isinstance(envelope["bib_name"], str)
    assert isinstance(envelope["items"], list)
    assert isinstance(envelope["errors"], list)
    assert all(isinstance(error, str) for error in envelope["errors"])
    # A failure has to say something in the documented failure channel, or a
    # consumer that branches on `errors` sees a failed command with nothing
    # wrong.
    if envelope["status"] == "error":
        assert envelope["errors"], f"`pzi {command}` failed with an empty errors[]"


def _render_envelope_keys(tmp_path: Path) -> str:
    lines = []
    for command in sorted(INVOCATIONS):
        envelope = _run_json(command, tmp_path / command.replace(" ", "-"))
        lines.append(f"pzi {command} [{envelope['status']}]")
        lines.extend(f"    {key}" for key in sorted(envelope))
        lines.append("")
    return "\n".join(lines)


def test_the_envelope_keys_match_their_snapshot(tmp_path: Path) -> None:
    """Pins the per-command keys, which `build_envelope` passes through freely.

    If this failed because of a change you meant to make, a `--json` field
    changed name or appeared or vanished — that is a frozen contract at 1.0.
    Say so in the commit subject, then regenerate:

        PZI_UPDATE_JSON_ENVELOPES=1 pytest tests/test_json_envelope.py

    Read the diff first. Regenerating without reading makes this a record of
    what happened rather than of what was decided.
    """
    current = _render_envelope_keys(tmp_path)
    if os.environ.get(UPDATE_ENV) == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
        return
    assert SNAPSHOT.exists(), (
        f"{SNAPSHOT} is missing — regenerate with {UPDATE_ENV}=1 pytest {__file__}"
    )
    assert current == SNAPSHOT.read_text(encoding="utf-8"), (
        "a `--json` envelope changed shape. Consumers branch on these field "
        "names; they are frozen at 1.0.\n"
        "If the change is intended, say so in the commit subject and regenerate:\n"
        f"    {UPDATE_ENV}=1 pytest {__file__}\n"
    )
