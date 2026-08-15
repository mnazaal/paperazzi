"""The public Python API, pinned — item 426.

Decision 12 made `import pzi` a supported surface rather than an accident, so
its shape is now a compatibility promise: after 1.0 a renamed parameter or a
changed default is a major version bump. This is the mechanism that makes that
promise mean something.

It is also the only surface in the 1.0 freeze whose consumers are not this
repository — everything else (CLI, config, HTTP routes) is used by the user's
own machine. Worth remembering when this file's snapshot next fails.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

import pzi
from pzi.errors import PziError

SNAPSHOT = Path(__file__).parent / "fixtures" / "public_api.txt"
UPDATE_ENV = "PZI_UPDATE_PUBLIC_API"

ONE_ENTRY = """@article{smith2020,
  title = {A Title},
  author = {Smith, Jane},
  year = {2020},
}
"""


def _library(tmp_path: Path) -> str:
    bib_path = tmp_path / "ml.bib"
    bib_path.write_text(ONE_ENTRY, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[[bibs]]\nname = "ml"\npath = "{bib_path}"\ndefault = true\n',
        encoding="utf-8",
    )
    return str(config_path)


#: `package_version`'s `lookup_version` default is a function object, so its
#: repr carries a memory address that differs on every interpreter run. Scrubbed
#: rather than excluded: *that the default is a function* is part of the frozen
#: signature, while *where it happened to be allocated* is not.
_MEMORY_ADDRESS = re.compile(r" at 0x[0-9a-f]+>")


def _render_surface() -> str:
    lines = []
    for name in sorted(pzi.__all__):
        obj = getattr(pzi, name)
        signature = str(inspect.signature(obj)) if callable(obj) else "(not callable)"
        lines.append(f"pzi.{name}{_MEMORY_ADDRESS.sub('>', signature)}")
    return "\n".join(lines) + "\n"


def test_the_public_api_matches_its_snapshot() -> None:
    """Fails on any change to `__all__` or to a public signature.

    If this failed because of a change you meant to make, it is a change to a
    **frozen public API**: after 1.0 that is a major version bump, not a patch.
    Record it in `CHANGELOG.md`, then regenerate:

        PZI_UPDATE_PUBLIC_API=1 pytest tests/test_public_api.py

    Read the diff first — a renamed keyword argument breaks every caller
    silently at import time, not at review time.
    """
    current = _render_surface()
    if os.environ.get(UPDATE_ENV) == "1":
        SNAPSHOT.write_text(current, encoding="utf-8")
        return
    assert SNAPSHOT.exists(), (
        f"{SNAPSHOT} is missing — regenerate with {UPDATE_ENV}=1 pytest {__file__}"
    )
    assert current == SNAPSHOT.read_text(encoding="utf-8"), (
        "the public API changed. `import pzi` is a frozen surface at 1.0.\n"
        "If the change is intended, record it in CHANGELOG.md and regenerate:\n"
        f"    {UPDATE_ENV}=1 pytest {__file__}\n"
    )


def test_every_public_name_is_importable_and_documented() -> None:
    """`__all__` is the promise; a name in it that does not resolve is a lie."""
    for name in pzi.__all__:
        obj = getattr(pzi, name, None)
        assert obj is not None, f"pzi.{name} is exported but does not exist"
        assert obj.__doc__, f"pzi.{name} is public but undocumented"


def test_py_typed_still_ships() -> None:
    """The annotations are only useful if a caller's type checker sees them.

    `py.typed` is a zero-byte marker, which makes it exactly the kind of file a
    packaging change drops without anyone noticing — and its absence is silent:
    the API keeps working, it just stops being typed for everyone downstream.
    """
    marker = Path(pzi.__file__).parent / "py.typed"
    assert marker.exists(), "py.typed is gone — the public API is no longer typed"


# --- Behaviour: the facade returns data, and raises rather than reporting -----


def test_the_read_only_functions_return_data_not_envelopes(tmp_path: Path) -> None:
    """Exercised through `import pzi`, not through the services underneath.

    A facade tested against its own internals proves only that the internals
    agree with themselves; what is being promised here is the import path.
    """
    config = _library(tmp_path)

    matches = pzi.search(query="Title", config=config)
    assert [m["citekey"] for m in matches] == ["smith2020"]

    listed = pzi.entries(config=config)
    assert [e["citekey"] for e in listed] == ["smith2020"]

    assert "@article{smith2020" in pzi.export(config=config)
    assert '"citekey"' in pzi.export("json", config=config)

    report = pzi.dedupe(config=config)
    assert report["status"] == "ok"


@pytest.mark.parametrize(
    ("call", "expected_code"),
    [
        (lambda config: pzi.search(query="x", config=config, library="nope"), 5),
        (lambda config: pzi.export("yaml", config=config), 2),
        (lambda config: pzi.search(config=config), 2),
    ],
)
def test_failure_raises_with_the_exit_code_the_cli_would_have_used(
    call, expected_code: int, tmp_path: Path
) -> None:
    """Silence on failure is the wrong default for a library.

    The services return `{"status": "error"}` because the CLI and HTTP API both
    render it; a caller who forgets to check that gets no data and no
    complaint. The facade raises instead, carrying the same classification a
    shell script branching on `$?` would see.
    """
    config = _library(tmp_path)
    with pytest.raises(PziError) as excinfo:
        call(config)
    assert excinfo.value.code == expected_code


def test_search_refuses_without_a_filter_in_its_own_words(tmp_path: Path) -> None:
    """The refusal must not name CLI flags a library caller never typed.

    The service words this as "provide at least one of --query, --author,
    --year, --tag". Everything below the facade still speaks CLI — an
    unresolvable `library=` still reports itself as `--target` — which is a
    known wart, but this one is the facade's own precondition to state.
    """
    with pytest.raises(PziError) as excinfo:
        pzi.search(config=_library(tmp_path))
    assert "--" not in str(excinfo.value), str(excinfo.value)
